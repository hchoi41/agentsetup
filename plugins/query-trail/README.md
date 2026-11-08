# query-trail

A Claude Code plugin that answers one business question with SQL on BigQuery so that every step can be
audited afterwards. Three rules (design spec v2, section 0.1):

1. **Code decides every pass and fail.** A script's exit code is the verdict; a model drafts, authors and explains.
2. **Max decides at named gates.** Each human decision is a numbered gate with a row in `decisions.csv`.
3. **Every step leaves a file.** What the file holds depends on the confidence level.

It does not make a query correct; it makes errors visible and restricts the report's wording (spec 4.4).
Governing documents: `../20261107_create_process_data_query_checker_design_spec_v5.md` (v1 to v4 kept as the
historical record) and `../20261107_create_process_data_query_checker_design_decisions_v1.md` (sections 4 to 11 are the build
rulings).

## What it does, phase by phase

| Phase | Scripts (D0) | Model or Max | Gates |
|---|---|---|---|
| (start) | `run_init.py` | Max names the run id, level, env file | |
| P0 Preflight | `preflight.py` | G0a questions | G0a, G0b |
| (question) | | Max states the question in his words (`00_question.txt`) | |
| P1 Cards | `card_freeze.py`, `registry.py` | card drafts from his question; Max freezes | G1a, G6 |
| P2 Discovery | `discover.py` (plan, build, lock, decide), `warehouse_jobs.py discovery`, `trap_scan.py`, `value_map.py`, `card_freeze.py --gate G1b` | discovery terms, value-map proposals | G2, G3, G1b, G13, G8, G15 |
| P3 Pin plan | `pin_plan.py` | batch marker | G13 |
| P4 Author | `packet_freeze.py`, `contract_check.py`, `spawn_scrubbed.py` | Claude writes B blind from `packet/` (A opt-in) | (G8) |
| P5 Decompose | `split.py`, `lint.py`, `compile.py` | one sentence per step (`step_sentences.json`) | G9, G10, G11 |
| P6 Run | `run_steps.py`, `replay.py` | narrates | G15, G8 |
| P7 Reconcile | `reconcile.py` | explains | G8, G15; G4 for reference differences only |
| P8 Sample | `sample.py`, `trace.py`, `warehouse_jobs.py trace` | trace plan, sentences | G12, G8, G15 |
| P9 Logic | `warehouse_jobs.py logic`, `logic_tests.py`, `reference_import.py` | proposes tests | G13, G8, G15, G4 |
| P10 Rename | `rename_apply.py` | Max per column (`/query-trail:rename`) | G5 |
| P11 Report | `report.py`, `secret_scan.py`, `view.py`, `close.py` (dry run) | quotes the verdict | G16, G6 |

P7: a B vs chain(B) or A vs chain(B) FAIL is never accepted at G4; it routes to P5 (a new attempt) or stops with
a G8 reason, and the verdict stays "not reconciled: decomposition differs". A comparison that compared nothing
(INCONCLUSIVE) routes to G8, or to G15 when the stream cap caused it. G4 answers reference differences only, with
the comparison's `--check-id` and a `--category`.

Every gate answer is recorded by `decide.py` (the only writer of `decisions.csv`). Exit codes, shared by all
scripts: 0 PASS, 1 check FAIL, 2 REFUSED, 3 a human decision is needed (`gate_request.json` written), 4 engine or
environment error. Exit 2 means "refused"; stderr then says (spec v4 1.4): "refused; no warehouse call was made" (or
the warehouse jobs that ran, counted as budget-ledger entries; a script with an engine may also have made free dry
runs and metadata reads before refusing), and "local effects: <the run files written, changed or removed>" or
"local effects: none". Preflight's offline refusals write no attempt file and say so; its exit 2 "identity is not
read-only" follows a CREATE TABLE dry run that BigQuery accepted (nothing was created: the probe is a dry run). Exit 2
alone never proves that nothing happened; read stderr.

**Secrets scan (P11, spec 7.3).** `secret_scan.py` fails the run (exit 1) on a secret pattern anywhere, and on an
env-file identifier or a project-qualified table name anywhere except Max's own words: `00_question.txt` and the
card's verbatim quote of it: any line of `01_question_card.md`, wherever it sits, whose normalized text (without a
leading `> `, list markers or the `question:` key) appears word for word in `00_question.txt`; no `>` prefix is
required (round 7). Those are classified `quoted_by_max` and reported as INFO lines (file, line,
identifier class) in `secret_scan_report.json`, never as a failure. `close.py --redact-quoted [--env-file F]` writes
`00_question.redacted.txt` and `01_question_card.redacted.md` with placeholders beside the frozen originals, for
export.

## Layout

```
.claude-plugin/plugin.json   no mcpServers
skills/<14 skills>/SKILL.md  run, rename and view are Max-only; 11 phase skills are user-invocable: false
scripts/*.py                 one CLI per D0 step; run_init.py starts a run, reference_import.py brings in a reference
scripts/lib/                 state, runctx (run folder, decisions, checks table), csv_contract, canon, bq (transport
                             and the budget ledger), levels, manifest (seal), sampling, envfile, sqltext, schema_check
hooks/hooks.json, hooks/*.py gate.py, edit_guard.py, csv_check.py, stop_check.py (stdlib Python); run_hook.sh (fallback)
config/                      levels.json, allowlist.json, run_config.schema.json, manifest.schema.json
compat/                      engine-pair binding for replay
tests/unit, tests/mutants, tests/hooks, tests/live
```

## Install for a session

Nothing at hub level is changed. Either:

- `claude --plugin-dir "<your install folder of query-trail>"`, or
- the hub junction route, after Max approves it (spec 1.6, open question 9): a real copy emitted by the sync
  script's `plugin` target; installed plugins do not read junctions (T14). Not done yet.

## Names used in the commands

| Name | Meaning |
|---|---|
| `QT_HOME` | Environment variable; default `%USERPROFILE%\.query-trail`. Holds `env\<env>.json`, `runs\<run_id>\` and `active_run.json` (the hooks read it to find the active run). Under Git Bash write it with forward slashes (`C:/Users/<you>/.query-trail`): `ls` and `cat` fail on a mix of `\` and `/`, and `run_init.py` prints `RUN=` and `QT_HOME=` in that form. |
| `QT_PY` | Environment variable: the pinned interpreter, a Python 3.10+ with this plugin's requirements installed (for example `<your envs folder>\query_checker\Scripts\python.exe`). `QC_PY` (the name the research `bq_env.sh` uses) is accepted as an alias. Scripts run as `"%QT_PY%" -I -X utf8 -B <script>`. |
| `PLUGIN` | Your install folder of this plugin, the folder that holds `scripts/` and `skills/` (set it in the shell before the commands below; the skills derive it from their own folder). |
| `RUN` | The run folder: `<QT_HOME>\runs\<run_id>\`, made by `run_init.py`. Every `--run-dir` argument is this folder. |
| `ENV_FILE` | The env file (identifiers only, D-12): `<QT_HOME>\env\<env>.json`, or a `bq_env.sh`-style file. |

**Env file fields.** JSON: `project`, `location`, `service_account` (the identity to impersonate),
`datasets` (in-scope dataset ids), `placeholders` (`{"project": <id>, "ds": <dataset>}`; SQL carries
`{{project}}.{{ds}}.table` only). Shell form: `BQ_PROJECT`, `BQ_LOCATION`, `QT_SERVICE_ACCOUNT`,
`BQ_SCRATCH_DATASET` (or `QT_DATASETS`, comma-separated). `tests/unit/test_no_identifiers.py` fails if a project ID
or service-account address appears in this folder, so the examples below use angle-bracket stand-ins.

Example `%USERPROFILE%\.query-trail\env\work.json` (replace each `<...>`):

```json
{
  "project": "<project-id>",
  "location": "US",
  "service_account": "<reader-name>@<project-id>.iam.gserviceaccount.com",
  "datasets": ["<sales_dataset>", "<ref_dataset>"],
  "placeholders": {"project": "<project-id>", "ds": "<sales_dataset>", "ref": "<ref_dataset>"}
}
```

The same in the shell form (`work.sh`, read without being run):

```
BQ_PROJECT=<project-id>
BQ_LOCATION=US
QT_SERVICE_ACCOUNT=<reader-name>@<project-id>.iam.gserviceaccount.com
QT_DATASETS=<sales_dataset>,<ref_dataset>
```

## Start a run

cmd.exe:

```
set QT_PY=<your envs folder>\query_checker\Scripts\python.exe
set PLUGIN=<your install folder of query-trail>
"%QT_PY%" -I -X utf8 -B "%PLUGIN%\scripts\run_init.py" --run-id rev_per_customer_01 --level 60 --env-file "%USERPROFILE%\.query-trail\env\work.json"
```

PowerShell:

```
$env:QT_PY = "<your envs folder>\query_checker\Scripts\python.exe"
$env:PLUGIN = "<your install folder of query-trail>"
& $env:QT_PY -I -X utf8 -B "$env:PLUGIN\scripts\run_init.py" --run-id rev_per_customer_01 --level 60 --env-file "$env:USERPROFILE\.query-trail\env\work.json"
```

Or let the run skill do it: `/query-trail:run level=60 run=rev_per_customer_01` (with the hooks active, the gate
hook lets `run_init.py` through although it has no `--run-dir`).

`run_init.py` creates `RUN`, writes `run_state.json` (P0, pending) and `run_config.json` from the schema defaults
(caps 5 GiB per job and 50 GiB per run, local CSV 1 M rows or 100 MB at level 90, stream 10 M rows or 1 GB,
retention 6 months, reference none, `required_checks: null` = the spec 6.4 default, single author), records only
the env name (never an identifier), activates the run for the hooks and prints `RUN=` and `QT_HOME=` with forward
slashes and the next command. `--home DIR`
overrides `QT_HOME` (then set `QT_HOME` to the same folder for the hooks). It refuses (exit 2, nothing written) a
bad run id or level, a missing or invalid env file, and an existing run folder.

Then, in Claude Code: `/query-trail:run run=<run id or RUN folder>` (a bare id means `<QT_HOME>\runs\<id>`). P0
asks G0a first: plan route (`system_time` or `csv_only`), ZDR and employer approval (`yes`, `no`,
`not_applicable`), retention in months (6 is the default), default level (60 to 100) and author mode (`single`,
`second:codex`, `second:max`); `decide.py --gate G0a ...` writes the approval block into `run_config.json`. Before P1
the run skill asks your question in your own words and keeps it verbatim in `00_question.txt`; the cards are drafted
from it.

Between G0a and G1a you may edit `run_config.json` (level, author mode, `sample_n`, caps, reference and its
provenance, `job_labels`, `pin`), or have the session edit it on your words; G1a hashes it with the cards and the
edit guard blocks changes afterwards (a changed configuration is a new run). **Caps rule:** the per-job cap
`caps.warehouse_bytes_per_job` must not exceed the per-run budget `caps.warehouse_bytes_per_run`; preflight refuses
otherwise (exit 2, before any warehouse call) and names the fix. Lowering the budget below 5 GiB therefore needs the
per-job cap lowered too.

**Declare a reference** (before G1a, in `run_config.json`), for example a control total from another system:

```json
"reference": {
  "type": "control_total",
  "independence": "independent",
  "source": "finance control total, monthly close pack",
  "extraction_method": "exported by finance from the GL, not from this warehouse",
  "as_of": "2026-10-31",
  "grain": "order_month",
  "basis": "booked revenue, KRW, close date",
  "tolerance": 0.005
}
```

`reference_import.py` checks four of these against its flags: `type` (anything but `none`), `source` and `as_of`
(equal to `--source` and `--as-of`) and `independence` (`independent` only with `--independence IND-5`;
`same_source` or `synthetic` with IND-0 to IND-4). `grain` names the columns the extract must carry. The schema needs
all eight fields once `type` is not `none`.

**Bring in a reference extract** (after G1b, before P9), when `run_config.reference` declares one:

```
"%QT_PY%" -I -X utf8 -B "%PLUGIN%\scripts\reference_import.py" --run-dir "%RUN%" --file extract.csv --source "<as in run_config>" --independence IND-5 --as-of "<as in run_config>"
```

The file must be UTF-8 without BOM, LF, with `reference_value` plus output-contract columns, including every grain
column (`reference.grain`, else the contract's grain or keys; a missing one is refused).
It is copied unchanged to `reference_values.csv` with a typed sidecar and its sha256, and the provenance goes to
`manifest.json` (`reference`). IND-5 only for an independent reference (GL trial balance, control total from
another system); IND-0 to IND-4 for same-source or synthetic ones (spec 4.3).

## Confidence ladder (spec 3.1, D-21)

| Level | Each step writes | Replayable |
|---|---|---|
| 60 quick (default) | row count, schema sidecar, multiset hash | none |
| 70 medium | + COUNT and SUM by grouping columns | none |
| 80 high | + 1,000-row hash-rank sample and strata rows | none |
| 90 extra high | full CSV under the local cap (1 M rows or 100 MB), else level 80 | steps whose inputs are retained |
| 100 max | full CSV for every step | every non-read step with retained inputs and supported types (spec v3 5.5: BIGNUMERIC is never replayed) |

The deliverable output is a full CSV at every level. The step jobs are the same at every level; trace lookups and
sample fetches grow with the level (slice: 247 lookups at 100 vs 9 at 60 before batching), so total cost does too.

## Budget (spec v5 5.3)

One ledger per run in `manifest.json` `budget` (`ledger`, `spent_bytes`, `held_bytes`, `budget_bytes`,
`remaining_bytes`), kept by
`lib/bq.py`: `BigQueryEngine` refuses any job (exit 2) unless the run ledger is attached (`bq.metered` or `ledger=`;
only the live transport test passes `allow_unmetered=True`), so every job of every phase (P0 CREATE TABLE dry runs, P2
discovery, P6 steps, whole B/A and rows-in COUNT jobs, P8 trace lookups, P9 logic inputs) goes through it.

- **Admission.** Before a job, `reserved = dry-run bytes + 10 MiB x distinct tables referenced` (BigQuery's 10 MiB
  per-table minimum may apply to every table on top of what the dry run counted). If `spent + held + reserved`
  exceeds `caps.warehouse_bytes_per_run`, the script opens G15 (filter, raise the cap, or stop; exit 3) and the job
  does not run. The estimate only admits; it is not what the ledger holds.
- **Authorisation (v4).** `authorized = min(per-job cap, remaining budget)`; the job is sent with
  `maximum_bytes_billed = authorized`, so BigQuery itself refuses (and bills nothing for) a job that would pass it.
  The ledger entry is written before the job is submitted, with `authorized_bytes`, `bytes_billed` null and
  `reconciled_utc` null, and it holds `authorized` until the bill is reconciled.
- **Reconciliation.** After the job the entry records the actual bytes billed and `reconciled_utc` (0 when BigQuery
  refused it over its cap). When the bill is unknown (the job failed after submission, or the process died) the
  entry keeps holding `authorized`, so `remaining = budget - spent - held` never lets a later job pass the budget
  because of it. Each entry carries the phase of the script that ran it; retries and new attempts count. P6 also
  checks the sum of all its reservations up front, before anything is billed.
- **Entries from an older plugin (v5).** An entry without `authorized_bytes` and without a recorded bill (no
  `reconciled_utc`) is unresolved legacy. It enters the budget arithmetic only when its historical submitted cap
  (`maximum_bytes_billed`) is recorded, and then holds exactly that cap. Without it, no new job is admitted (exit 2,
  naming the entry: "no command records a legacy bill: close this run with close.py and start a new run"), whatever reservation it recorded and
  whatever the current per-job cap is: a reservation is an estimate, and today's cap need not have bounded that old
  submission. A legacy entry with a recorded bill counts that bill. `report.md` shows
  the held bytes beside spent / budget.
- **One script at a time per run.** The ledger is a read, extend, rewrite of `manifest.json` (entries another
  script wrote are re-read before each write); the orchestrator runs phases one after another, so do not run two
  scripts on one run in parallel.

Dry-run sums alone are not a budget (slice: 16.75 MB estimated vs 398 MB billed for the step jobs).

## Rows in, rows out (spec v2 2.2 P6)

`run_steps.py` records per step `rows_in` (read step: the source COUNT(*) at the pin, from the step job when the
read has no filter, else one cheap pinned COUNT job; other steps: the input steps' rows_out, the larger input for
a join, the sum for a union), `rows_out`, `row_delta_reason` (`read`, `filter`, `aggregate`, `join_fan_out`,
`join_reduce`, `join_mixed`, `project`, `dedupe`, `union`, `unexplained`) and `row_delta_source` (`split_op`,
`g11_sentence`, `max`). A join that increases rows beyond its larger input, or a step whose count moves the wrong way,
is `unexplained`: the required check `p6_step_NN_rows_in_out` FAILs and only a G8 row with that check id, given in
the same attempt, explains it (unless Max approved, at G11, a step sentence that announces the fan-out). A join whose
count lands strictly between its smallest and largest input, or whose announced fan-out did not raise the count, is
`join_mixed`: the count fits a fan-out masked by a filter as well as a plain reduction, and counts cannot tell them
apart.

## Rights (spec v4 2.2 P0)

The identity's grant is the boundary. P0 records an observation of that grant, not a proof of it, and nothing in
the plugin certifies the identity. The only rights wording written is the observation sentence, with its time:
**"On <time>, no write permission was returned by tables.testIamPermissions on N in-scope tables; CREATE TABLE dry
run was refused for bigquery.tables.create in each of M in-scope datasets; project-level mutation permissions <tested: list (none held) |
UNAVAILABLE (reason)>."** A CREATE refusal reused from an earlier preflight attempt is labelled "reused
observation from <time>". The observations:

- `tables.testIamPermissions` on every table of every in-scope dataset (the listing is paged through, no cap; the
  count per dataset is recorded) shows no write permission. The API returns which of the requested permissions the
  caller holds on the table, inherited grants included
  ([API reference](https://cloud.google.com/bigquery/docs/reference/rest/v2/tables/testIamPermissions)); it does
  not rule out creating new tables or datasets, hence the next two items.
- A CREATE TABLE **dry run** is refused (403, `bigquery.tables.create denied`) in **every** in-scope dataset (fix r6:
  `dry_run=True`, so BigQuery checks the permission and creates nothing, bills nothing; one per dataset). The plugin
  never submits a non-dry-run write statement, P0 included, and there is no option to make it do so. A dry run that
  is accepted means the identity can create tables: it is recorded `write_possible: true` and preflight exits 2
  ("identity is not read-only"). `00_preflight.json` records `rights.write_smoke.mode: dry_run`; the ledger keeps one
  0-byte entry per probe (outcome `dry_run_refused`, or `write_possible`). Fix r9: only a 403 whose message names
  `bigquery.tables.create` counts as the refusal; a generic `accessDenied` without that permission name, or any
  other reason (`rateLimitExceeded`, `quotaExceeded`, a `jobs.create` denial), leaves that dataset UNVERIFIABLE and
  preflight records the raw reason (redacted). The probe runs once per identity per run;
  a preflight rerun reuses the earlier refusal (`--force-smoke` repeats it).
- `projects.testIamPermissions` on `bigquery.datasets.create`, `bigquery.datasets.update`, `bigquery.tables.create`,
  `bigquery.tables.update`, `bigquery.tables.updateData`, `bigquery.tables.delete` and `bigquery.jobs.create`, each
  recorded; a mutation permission held fails. When the Cloud Resource Manager API is off it is recorded UNAVAILABLE
  (as in the research project) and the observation sentence says so.

A table listing that fails, or any failed table test, is UNVERIFIABLE (exit 2). A dataset with no table is recorded
and skipped by the table test (the sentence names it); no table in any in-scope dataset is UNVERIFIABLE. The
dataset access list is supplementary: the identity listed with a write role fails; listed read-only passes that
row; not listed directly (a group or project grant) is UNVERIFIED, never PASS. `00_preflight.json`
`rights.observation` records the basis, the result, `observed_utc` and the `statement`. What it does not cover:
datasets outside the env file's list, project-level grants when the CRM API is off, and any change to the grant
after the observation time.

## Retention dates (spec v5 7.1)

Every `manifest.json` `files[]` entry carries `created_utc` and `created_basis`. `generated`: the script that wrote
the file took the time when it wrote it (P6 outputs, the reference sidecar). `inferred`: a file registered from
elsewhere or written earlier by another step (step SQL, executed SQL, the reference extract, the P7 to P11 outputs
registered at P11); its `created_utc` is the earliest of the file's birth time (where the OS gives one), its
modification time and its registration time. An inferred date is not creation provenance: an old file modified just
before its first registration, or a copy that reset its timestamps, gets a recent date. The only guarantee is that
nothing after first registration extends retention: once written, `created_utc`, `created_basis`, `registered_utc`
and `retention_until` never change, whatever later modification or re-registration happens. Before registration,
`created_basis: inferred` is a best effort and may be later than the true creation (a late registration of a file
with a future modification time, or with no birth time, gets the registration time). A `generated` file carries
its true creation time.

Files not in the manifest are never touched by `close.py`: `decisions.csv` (the gate log, outside the seal on
purpose), `gates/`, `run_state.json`, the manifest and its seal, and `view.html` (the run viewer, below).

## Viewer (round 13)

`view.py --run-dir RUN [--out PATH]` writes `RUN/view.html`, one page to open in a browser from disk. It shows the
run header (the verdict quoted word for word from `report_status.json`, pin, budget), the P0 to P11 strip with each
phase's files and gate rows (Max's answers verbatim; a superseded row struck through), the step graph drawn from
`plan.json` (click a step, or Tab to it and press Enter, to read its sentence, SQL, executed SQL, rows in and out,
job and P6 checks), whole query B with the first 200 rows of the deliverable and the P7 reconciliation, every check
with a filter box, the cards and inputs, and every file of the run as a relative link. A run that stopped early
renders too; what is missing is named ("P7 not reached"). The page loads nothing from the network: CSS and script
are inline. From a `file://` page the browser opens the linked `.sql`, `.csv`, `.json` and `.md` files as text.
The browser window decides the width: no fixed page width. The graph fits its box only while that keeps it at 80%
of its size or more; otherwise it opens at actual size (12 px text) and scrolls sideways inside its own box ("Fit to
window" / "Actual size" switch it). Each graph column is as wide as its longest label, and whole B and the
deliverable sit under the final step. The step panel sits beside the graph and moves under it below 900 px; tables
and SQL scroll inside their own boxes.

The page is derived: it is not in `manifest.json`, the seal does not cover it and `close.py` never deletes it.
`secret_scan.py` scans it like any run file (round 15); its copy of the question counts as Max's own words, so it is
reported as INFO, never as a hit. Until a scan newer than every run file exists, the page opens with "Not yet
secret-scanned: do not share this page". `run_state.json`, `gates/` and the page itself are state, not content, and
do not count; a new `decisions.csv` row (Max's answer text) does. `secret_scan.py` also runs on a closed run (P11
done; round 15b), so the close ends with a scan and a fresh page after the G16 row, and `/query-trail:view` scans a
run at P11 before rendering it. The verdict is labelled "From an earlier pass, not current" when the run has
stepped back before P11, the seal no longer matches `manifest.json`, or the report is from another attempt.
`view.py` refuses (exit 2, nothing written) without `run_config.json` or `run_state.json`, while
`secret_scan_report.json` lists hits in a run file other than `view.html` itself, and for an `--out` inside another
run folder or under `QT_HOME/runs`. The same run folder gives the same bytes; rerun it after any change (the report
skill runs it at P11; `/query-trail:view [run=...]` runs it for any run, which is why the gate hook lets `view.py`
read a run other than the active one).

The first `report.md` of a run does not list `view.html` in `## Files`: `report.py` writes and seals the report
before the viewer exists (the viewer runs after the secrets scan). Rerunning `report.py` only to add that line would
reseal and need a fresh scan, so the seal rule is kept as it is and the close message gives the viewer's path from
`view.py` instead. A later `report.py` run lists it.

## Discovery (spec v5 2.2 P2)

The session writes `discovery_terms.json` (shape: `config/discovery_terms.schema.json`; a wrong shape is refused
naming the field and its shape) and runs `discover.py plan`, `warehouse_jobs.py discovery`, `discover.py build`; Max
is asked at G2 with the `g2_sentence` of `discover_status.json` (shortlist, refused sources, dropped tables). His
answer is recorded with `decide.py --gate G2 --outcome approve --tables a,b` (v5: input_hash = sha256 of the sorted
table list in placeholder form, LF-joined); `discover.py lock --tables a,b` refuses any other set ("table set
differs from the G2 decision: ask G2 again with --supersede") and records the kept and the dropped tables. Nothing
is profiled, trap-scanned, decided or pinned before that lock, and every later P2 and P3 step refuses while the lock
and the current G2 decision disagree, or a table outside the lock. The value map is written only by `value_map.py
add` (kinds `timezone`, `period`, `filter`, `derive`, `map`, `not_used`; a period is whole days, both ends
included); a timezone trap clears only for the column a row names (or with a run-wide row that has its own G3
decision row). Once the card has bindings, timezone and semi-additive traps block only on columns the bindings or
the value map use; other columns are listed `not_bound`. A `not_used` row clears its column's traps and is never a
filter. A G3 row whose `--check-id` is a trap id resolves that trap. A freshness disagreement on a locked table
opens G13 (one check id per table, `freshness:<table>`) unless a `period` or `not_used` row names that table and
its date column, or a G13 `answered` row with that check id exists; the shortlist and 13_table_decision.md say
which. `discover.py decide` writes `13_table_decision.md`, the packet's table decision, before P2 is left (and
again at P3 or P4 until the packet is frozen: `packet_freeze.py --freeze packet` refuses a decision file that
differs from what decide writes now). Only filter rows and the bindings' date boundaries are written as filters;
freshness-settling period rows and not_used rows are listed under Freshness, "settled; not a filter".

## Gates

G0a use approval, G0b preflight, G1a card intent, G6 registry, G2 tables, G3 value map, G1b bindings and output
contract, G8 failure or disagreement, G13 data-owner question, G9 lint waiver, G10 refused construct, G11
one-sentence steps, G15 cap or budget breach, G4 reference differences, G12 trace sign-off, G5 rename, G16 close
acknowledgement (spec v2 2.1). The Stop hook refuses to end a turn while `gate_request.json` is unanswered.

What code enforces (`decide.py`, `state.py set`; maps in `scripts/lib/state.py`): a gate is recorded only at
its phase (spec v2 2.1, plus G8 at P0 and P4, G15 at P2, P7, P8 and P9, G4 at P9, where documented flows ask
them); a gate a script opens through `gate_request.json` (G6, G10, G13 at P2 and P3, G15) only while that request
is open (a script that opened one archives it to `gates/stale_*` on a later exit 0: the condition is gone;
`trace.py` opens G8 with check id `p8_trace` on a trace FAIL, exit 3); a per-run gate the orchestrator asks (G0a, G0b, G1a, G2, G3, G1b, G11, G12, G5) once per attempt and
`--check-id`, a second answer only with `--supersede` (the row records `supersedes`); the phase moves forward one
step at a time and leaves P0, P1, P2, P5, P8 and P10 only after their per-run gates have a row in this attempt.
Every refusal is exit 2. After any non-zero exit
the orchestrator records `state.py set --blocked "<script> exit <n>: <reason>"` while the cause is fixed (the run
skill lists the route per phase for exit 1). What code cannot enforce: who answered. `--by` is free text; `answer_text` (`--answer`,
required) holds the human's words only if the run skill passes them verbatim. `decisions.csv` columns: gate,
phase, input_hash, outcome, reason, time_utc, check_id, decided_by, answer_text, attempt, supersedes, gate_request.

**B against its freeze.** `split.py`, `lint.py` (P5), `compile.py`, `run_steps.py`, `contract_check.py` (P6) and
`rename_apply.py` read `B.sql` (and `A.sql` in second-author mode) only after `lib/freeze_check.py` confirms the
packet digest and the file's sha256 against `packet_freeze.json`; otherwise exit 2, nothing written. `plan.json`
carries B's frozen sha256 as `source_sha256`, and compile and run_steps refuse a plan from another B. At P4 the
pre-freeze loop (`contract_check.py`, `lint.py --targets B`) reads the draft until B is frozen.

**A corrected B inside the run (v5).** After a decision of the current attempt that routes back to P4 (G8
`rewrite` or `change_course`, G9 `rewrite`, G10 `rewrite`, G11 `split_finer` or `reject`), `state.py set --phase
P4` may move back from P5 or later (refused without it), the edit guard lets `B.sql` change at P4, and
`packet_freeze.py --freeze B --new-attempt` re-freezes it: the attempt goes up by one, the old hash stays under
`packet_freeze.json` `B.superseded[]` with the decision row, and the old attempt's `steps/`, `plan.json`,
`executed/`, `checks/` and `lint_report.csv` move to `attempts/<n>/`, so split, lint and compile run again. The
packet stays frozen; a packet or card change is a new run.

**Plugin version (v5).** `run_init.py` stamps `plugin_version` (from `.claude-plugin/plugin.json`) into
`run_config.json`; every script and `decide.py` refuse a run started under another version: "run started under
plugin X; current Y; start a new run" (exit 2). A run_config without the stamp (a run started before v5) is unchecked: no version comparison is made for it.

**Decision binding (v5).** A G8 or G4 row explains a check only while its `input_hash` equals the check's
`evidence_sha256`, always the sha256 of `check_id|attempt|status|detail|<file sha256 or "-">`: an old explanation
stops applying once the check's status, detail, attempt or file changes.

## Hooks (spec v5 1.5)

| Event | Script | Blocks |
|---|---|---|
| PreToolUse Bash, PowerShell | `gate.py` | script calls without a valid run state (`run_init.py`, `state.py` init/activate and `compat_suite.py` excepted); `bq query` (except a dry run of one allowed SELECT/WITH), bq write verbs, the Python client from the command line; vendor CLIs (codex, copilot, claude, gemini, aider) not wrapped by `spawn_scrubbed.py`; writes to protected run files through `>`/`>>`, `sed -i`, `tee`, `cp`, `mv`, `rm`, and the PowerShell cmdlets `Set-Content`, `Add-Content`, `Clear-Content`, `Out-File`, `New-Item`, `Copy-Item`, `Move-Item`, `Rename-Item`, `Remove-Item` (and their aliases), also inside `bash -c` and `pwsh -Command` |
| PreToolUse `mcp__.*` | `gate.py` | BigQuery `execute_sql`, `cancel_job`; any BigQuery MCP tool during a run |
| PreToolUse Write, Edit, MultiEdit, NotebookEdit | `edit_guard.py` | writes outside the active run folder (plugin tests excepted); script-owned and frozen files (every preflight attempt file; `11_value_map.csv` and `13_table_decision.md`, written by their scripts; `00_question.txt`, `01_question_card.md` and `run_config.json` once G1a is approved in any attempt of the run, for
the rest of the run (later attempts inherit the approval and never repeat G1a; changing intent is a new run); the metric card and output contract after G1b; steps/ after an approve-class G11: approve, accept, signed) |
| PostToolUse Bash, PowerShell | `csv_check.py` | nothing; reports CSV-contract violations |
| Stop | `stop_check.py` | ending a turn with an open gate or a phase `in_progress` (honours `stop_hook_active`); after a script exited non-zero (1, 2 or 4), `state.py set --blocked "<reason>"` keeps the phase `in_progress` with the reason recorded and lets the turn end |

`state.py activate --run-dir R` (or `run_init.py`) points the hooks at the active run (`<QT_HOME>/active_run.json`).

**Fail closed.** Each hooks.json command is `PYTHONUTF8=1 python -I -X utf8 -B <hook> || "${BASH:-bash}" run_hook.sh
$? <hook>`. When `python` is missing, `run_hook.sh` tries `QT_PY`, `python3` and `py -3`; with none it exits 2
(blocks) with a message. When `python` started but failed, a PreToolUse hook blocks; Stop and PostToolUse report
and let the turn go on. A `python` that is the Microsoft Store alias blocks every call until the alias is turned
off or a real Python is first on PATH. **Limitation:** the commands need bash (Git Bash on Windows, which Claude
Code uses for hook commands); under cmd.exe the `PYTHONUTF8=1 python ...` form is not valid and the hook fails
open. The Bash and PowerShell matchers see only the command line, not what a Python script (or a .ps1 file, or a
.NET call such as `[IO.File]::WriteAllText`) does inside: every script also refuses without a valid state, and
scripts refuse changed files by hash.

## Troubleshooting

- **Every Bash call is blocked with "did not run cleanly under python" or "no Python interpreter".** The hooks
  fail closed by design. Most often `python` on PATH is the Microsoft Store alias (Windows\WindowsApps\python.exe):
  it exists, so no fallback is tried, and it never runs the hook, so every PreToolUse call is blocked until it is
  fixed. Turn the alias off (Settings > Apps > Advanced app settings > App execution aliases: python.exe and
  python3.exe off) or put a real Python 3.10+ first on PATH; with `python` absent altogether, `QT_PY`,
  `python3` and `py -3` are tried in turn.
- **A script exits 2 with "BigQueryEngine has no budget ledger".** A job was asked of an engine outside
  `bq.metered`; every plugin script meters its engine, so this means a hand-written caller.

## Tests

From this folder:

```
"%QT_PY%" -I -X utf8 -B -m pytest tests -q
(The test suite under `tests/` ships with the project copy; the public plugin payload on GitHub omits it, because its fixtures carry made-up service-account addresses and key-shaped strings that a public-repo secret gate would flag.)
set QT_LIVE=1 & set QT_ENV_FILE=..\bq_env.sh & <same command> tests\live -s
```

`tests/live` is skipped unless `QT_LIVE=1`; it reads every identifier from `QT_ENV_FILE` (or `BQ_PROJECT`,
`BQ_LOCATION`, `QT_SERVICE_ACCOUNT`, `BQ_SCRATCH_DATASET`). `tests/hooks` feeds JSON payloads to the hook
scripts in the T14 manner and runs the hooks.json command line under bash, with and without a `python` on PATH.

## Live-verified versus assumed

Live on BigQuery (thin slice 2026-11-08, `../build/slice_20261108/WALKTHROUGH.md`): the impersonated service
account with the table-level rights observation; pin FINAL on every base reference of every cumulative job;
split fidelity B vs chain(B) at levels 100 and 60; Arrow to DuckDB COPY with the row-count assertion; the ladder
artifacts; level-60 hashes equal to level-100 CSV hashes; replay at 100; the sampler; the report wording; the
four non-happy paths. Built after the slice and tested offline only (fix round 1): the budget ledger and its
reservations, rows-in COUNT jobs, batched trace lookups, CTE pruning in cumulative jobs, `run_init.py`,
`reference_import.py`, the hook fallback. Fix round 2, offline only: the reservation upper bound and the
`maximum_bytes_billed` bound by the remaining budget, the write probe in every dataset and the per-permission
project test, table paging without a cap, preflight's offline-first order and attempt files, PowerShell write
blocking, `join_mixed`, `created_utc` from birth time. Fix round 3, offline only: the authorised-amount ledger,
the observation-only rights wording, `created_basis`, the exit-2 wording, discovery input validation, the G2 lock
enforced through P3, `value_map.py`, per-column timezone traps, G13 at P2, `discover.py decide`. Fix round 4,
offline only: the evidence envelope for G8/G4 binding, legacy ledger entries, the G1a freeze across attempts, the G2
lock bound to the exact table set, composite and typed joins in source recompute, the `plugin_version` stamp,
per-table G13 answers, bound-column traps, `--freeze B --new-attempt`, stamped logic inputs, the p8_trace G8 exit.
Fix round 5, offline only: legacy entries admitted only with their recorded cap, per-(unit, measure) source coverage
(`measures_partially_recomputed`), step-to-step lookups NOT_APPLICABLE below level 90, card conditions parsed as SQL
at G1a and P9, samples/trace/logic inputs archived on a new attempt with stamped trace results. Fix round 6: the P0 write probe as a CREATE TABLE dry run was observed live (`build/dryrun_ddl_probe.py`); the rest of round 6 (pop_bands and MECE conditions as a WHERE, quoted-by-Max scanning, `--redact-quoted`, the headline claim by strength) is offline only. Fix round 7, offline only: the Rights wording and the quoted-line rule wherever it sits in the card. Rounds 4 to 7 were first validated offline (the dry-run probe is the one path observed live during the build); run session_l60_05 then exercised the paths listed below live on the round-7 tree. The session walk (2026-11-08, level 60, live) reached G1a on
the round-1 plugin. Exercised live by the closed run `session_l60_05` on the round-7 tree (P0 to P11 plus close at
level 60, single author, no reference): the preflight dry-run probe, the G2 lock, G13 settlement, pin, run_steps and
the ledger, reconcile, the source-recompute trace, the MECE and chain logic tests, G16, the secrets scan INFO path,
the close dry run. Still offline only: reference import and tie-out, B rewrite `--new-attempt`, the level 70 to 100
artifacts, a second author, the legacy ledger block, the G15 budget stop, redaction. Hooks verified live in a real Claude Code session on 2026-11-08: run `session_l60_06`, driven by Max in his own
session (plugin loaded with `--plugin-dir`, level 60, P0 to P11, G16 acknowledged): the gate hook on 55 Bash calls
(none blocked: only scripts were called), csv_check on 52, Stop on 10 turns with 3 blocks, each correct (an open
G0a, P1 in progress without an exit status, an open G1a); nothing timed out. The same run drove the skills from a
real Claude session for the first time. Fix round 11 (what run 06 surfaced), offline only: the packet's value-map
sidecar, the table decision refreshed at P3, the P1 question prompt, `aggregation_weight` against `chain.entity`, the
report's `## Files` section and its no-reference sentence, forward-slash `RUN=`/`QT_HOME=`, lint B2, the caps in the
G0a request. The plugin.json version is not bumped for round 11: the run folder's `plugin_version` stamp rule stays as
it is. Fix round 12 (cold review of round 11), offline only: lint B2 now reads the join kind. A LEFT JOIN that reads
no column of the table says "joined but unused: <table>; drop it at G2, or say which column it supplies". An INNER
(or plain), CROSS, RIGHT, FULL or comma join that reads no column says "INNER JOIN to <table> keeps only rows with a
match and no column of it is read; dropping it changes the result: confirm whether the filter is intended (bind it as
a filter row at G3, or drop the table at G2 if not)", with the join's own word and effect; it never says "drop it".
Also: `pin_plan.py` leaves `13_table_decision.md` alone once the packet is frozen, the `## Files` step range counts
only the numbered `executed/NN.run.sql` files (`executed/A.run.sql` gets its own line), and a run_config.json with no
`reference` block reads "No reference was declared (reference: none)". The plugin.json version is not bumped. Round 15
(cold review of rounds 13 and 14), offline only: the viewer's stale-verdict label, the scan of `view.html` and the
"not yet secret-scanned" banner, the round-14 gate rule on the page, `--out` kept out of other runs, node colours by
the spec 6.4 result, and the G1b weight confirmation bound to the output grain. Round 13, offline only: the run viewer `view.py` (`view.html`, derived and unsealed), the `/query-trail:view`
skill, `view.html` in the report's `## Files` when it exists. Rendered from run `session_l60_06` and checked in Edge
headless and Chromium at 1920, 1280, 800 and 600 px windows. Assumed or unverified: `user-invocable: false` behaviour; the edit hook (`edit_guard.py`) inside a live
session (run 06 made no Write or Edit call, so it never fired; tested headless only).

Round 14 (Max overturned the round-12 ruling): at G1b, a sealed `aggregation_weight: not_applicable` on a grouped output contract (non-empty `grain` in `03_output_contract.json`) stops `card_freeze.py` with exit 3 until Max confirms on the record that no combined total is wanted (`decide.py --gate G1b --outcome confirmed --check-id aggregation_weight:not_applicable`); the card is never edited, and a different weight is a new run. Round 14 also tightens phase exits: a per-run gate counts as answered only by a `decisions.csv` row with no `--check-id` whose outcome closes it (`state.CLOSING_OUTCOMES`: G0a approve; G0b accept; G1a, G2, G3, G1b approve; G11 approve, accept or signed; G12 signed, approve or accept; G5 approve or skip) and that no later row supersedes; a check-id row, a trap resolution or a lone reject, stop or decline does not let the phase move on.

## Known limits

- **Rights:** `projects.testIamPermissions` needs the Cloud Resource Manager API, which is off in the research
  project; the observation sentence then says "project-level mutation permissions UNAVAILABLE" (see Rights).
- **REST streaming**: the Storage Read API is not granted; results come back by REST paging (slower on big results).
- **N × S not measured on a large table**: on the frozen copy every job bills BigQuery's 10 MiB per-table minimum,
  so the measured chain ratio reflects the minimum, not scan volume. Cumulative jobs now carry only the CTEs a step
  depends on, so unused read CTEs are no longer billed.
- **WSL2 eval not run**: `claude plugin eval` needs WSL2 or Linux; native Windows refuses Bash grants (T14).
- Hooks are a policy layer, not the boundary (the identity's grant is).
- Trace lookups are batched per table (one job per table per batch of up to 200 key lookups, each lookup then
  evaluated locally on the batch rows); aggregating (many_to_1) lookups still run one job each.

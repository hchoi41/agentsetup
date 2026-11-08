---
name: preflight
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P0 (Preflight): use approval, environment, rights observation, caps and time-travel windows; called only by the query-trail run orchestrator."
user-invocable: false
---

# preflight (P0)

Purpose: confirm that the environment, the identity's rights and the caps are fit before anything reads the warehouse. Max's use approval (G0a) comes first; nothing is probed before it is recorded.

The identity's grant is the boundary; P0 records an observation of it, not a proof, and nothing certifies the identity (spec v4 2.2 P0). Quote only the observation sentence of `rights.observation.statement`, with its time: "On <time>, no write permission was returned by tables.testIamPermissions on N in-scope tables; CREATE TABLE dry run was refused for bigquery.tables.create in each of M in-scope datasets; project-level mutation permissions <tested: list (none held) | UNAVAILABLE (reason)>." A refusal reused from an earlier attempt reads "reused observation from <time>". The observations: `tables.testIamPermissions` on every table of every in-scope dataset (all tables, paged, no cap; a dataset with no table is recorded and skipped), a CREATE TABLE dry run (`dry_run=True`: nothing is created; the plugin never submits a non-dry-run write) refused in every in-scope dataset, and `projects.testIamPermissions` on `bigquery.datasets.create`, `bigquery.datasets.update`, `bigquery.tables.create`, `bigquery.tables.update`, `bigquery.tables.updateData`, `bigquery.tables.delete` and `bigquery.jobs.create` (or UNAVAILABLE). Never describe the identity as read-only.

Commands use `QT_PY`, `PLUGIN`, `RUN` and `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P0`

Exit 2: the orchestrator is elsewhere. Do nothing and return.

## 2. Preflight

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/preflight.py" --run-dir "$RUN" --env-file "$ENV_FILE"`

Order inside the script: G0a recorded; then the offline checks (run_config.json against its schema, the caps rule, the env file, the interpreter and its imports, the path length); only then the live probes (identity, rights, write smoke, time-travel windows); then the CSV self-test. An offline failure stops before any warehouse call.

| Exit | Meaning | Next |
|---|---|---|
| 0 | environment, identity, rights, caps, windows and self-test pass | hand G0b to the orchestrator |
| 3 | no G0a approval in run_config.json; `gate_request.json` G0a written (questions and value sets in `details`; the budget caps in `details.caps`, with the sentence to quote) | the orchestrator asks G0a (section 3); `decide.py` writes the approval block; rerun this command |
| 2, before any live probe | an offline check failed; stderr: "refused; no warehouse call was made; local effects: none (no attempt file, no 00_preflight.json)". Most often the caps rule: **the per-job cap (`caps.warehouse_bytes_per_job`) must not exceed the per-run budget (`caps.warehouse_bytes_per_run`)**; the message names both numbers and the fix (lower the per-job cap to at most the budget, or raise the budget) | tell Max the rule and the fix; he edits `run_config.json` (or you do, on his words); `state.py set --blocked "<reason>"` while he decides; rerun |
| 2, after live activity | a write right found, a CREATE TABLE dry run accepted in some dataset (`write_possible: true`, "identity is not read-only"), or the rights evidence is incomplete (fail closed): a table listing that fails, no table in any in-scope dataset, a failed `tables.testIamPermissions`, an unreadable access list, a write smoke not refused. stderr says "refused after live activity", lists the warehouse calls made and the local effects: the files written (`00_preflight.json`, `00_preflight.attemptN.json`, `00_capability_probe.csv`, `00_capability_probe.attemptN.csv`) and the ledger entries added | report the reason and that list; `state.py set --blocked "<reason>"`; stop the phase until Max fixes the identity or scope |
| 1 | a self-test surprise (CSV round trip or hash) | G8; the affected check is disabled and listed |
| 4 | ADC missing, imports missing, or engine error | tell Max; one unchanged retry after the fix |

Reruns: every preflight attempt keeps its own file (`00_preflight.attemptN.json`, `00_capability_probe.attemptN.csv`); `00_preflight.json` is the latest. The write smoke runs once per identity per run: a rerun reuses an earlier attempt's refusal for the same identity and dataset (recorded as `reused_from`) and puts no new entry on the ledger. Add `--force-smoke` only when Max asks for a fresh write test (for example after the identity's grants changed).

## 3. The G0a questions

Ask them through the orchestrator in one AskUserQuestion; never fill an answer in yourself. Open it with the caps Max is approving, from `gate_request.json` `details.caps.sentence`, in human units: "Budget caps for this run: 5 GiB per job, 50 GiB per run (run_config.json caps)" (the defaults; the request carries the run's own numbers). He may change them in `run_config.json` before G1a (run skill, "Editing run_config.json"). Each answer maps to one `decide.py --gate G0a --outcome approve` flag with a fixed value set (anything else is refused, exit 2, with the list on stderr):

| Question (plain words) | Flag | Allowed values |
|---|---|---|
| Plan route: may the warehouse be read at a fixed past time (time travel), or only from exported CSVs? | `--plan-route` | `system_time`, `csv_only` |
| Is zero data retention (ZDR) in place with the model vendor for this data? | `--zdr` | `yes`, `no`, `not_applicable` |
| Has your employer approved sending this company data to a model vendor? | `--employer-approval` | `yes`, `no`, `not_applicable` |
| How long should the run's files be kept (default 6 months)? | `--retention-months` | a whole number of months; equal to 6 it is recorded as the default, otherwise as an override |
| Default confidence level for this run? | `--level` | `60`, `70`, `80`, `90`, `100` |
| One author, or a second author (Codex or you)? | `--author-mode` | `single`, `second:codex`, `second:max` |

Example: `decide.py --run-dir "$RUN" --gate G0a --outcome approve --reason "<his words>" --by Max --answer "<his answer, verbatim>" --plan-route system_time --zdr not_applicable --employer-approval not_applicable --retention-months 6 --level 60 --author-mode single`. Pass only the flags he answered. Privacy and personal-data questions are not asked (decisions Q8: the exposure gate is secrets-only).

## Never

Probe anything (rights, datasets, jobs) before G0a is recorded. Owner mode (`owner_mode: research`) is only for the research project and is printed on every report as "owner mode, non-compliant with D-08, research only".

## Artifacts left

`00_preflight.json` and `00_preflight.attemptN.json` (rights probe with its `observation` block: basis, table test, write smoke per dataset with `observed_utc`, `observed_utc`, `statement`, `datasets_without_tables`; `project_test.tested` per permission or UNAVAILABLE; `tables_listed` per dataset; an access list that does not name the identity directly is UNVERIFIED, never PASS; windows, caps, versions), the write smokes on the budget ledger (`manifest.json` budget), `00_capability_probe.csv` and its attempt copy, the G0a block in `run_config.json`, decisions.csv rows G0a and G0b.

## Hands to

G0b (accept the technical preflight), then P1.

# Stage 2b notes: library changes needed and interfaces with stage 2a

Stage 2b did not edit `scripts/lib/`. Each item below is worked around locally; the proposal says where it belongs.

## Library changes proposed

1. **Shared CLI and CSV helpers.** `scripts/stage2b_common.py` holds a multi-phase phase check, the exit-code mapper, `write_records` / `read_records` (Python records through `csv_contract.write_query_csv` and `read_csv`), the `gate_request.json` writer, `add_months` and `resolve_in_run`. Stage 2a added `lib/runctx.py` with overlapping helpers (`write_rows_csv`, `run_cli`, `write_json`). Proposal: merge both into one `lib/runctx.py` and delete `stage2b_common.py`.
2. **Public names in lib.** Stage 2b uses `csv_contract._quote_ident` and `canon._INT`, `_FLOAT`, `_DEC`, `_STR`, `_TS_NAIVE`, `_TS_TZ`. Proposal: a public `csv_contract.quote_ident` and `canon.type_class(type_text) -> "int" | "float" | "decimal" | "string" | "date" | "timestamp" | ...`.
3. **One sampling module.** `run_steps.write_sample` (level-80 sample) and `sample.py` both compute row id = sha256(canonical row + 0x1f + occurrence index or file position) and rank = sha256(salt + 0x1f + row id), with `run_config.float_tolerance` in the canonical row and the run id as the salt fallback. `sample.py` recomputes and verifies `_qt_row_id` and `_qt_rank` and refuses on any difference. Proposal: move `row_id`, `rank_key` and the salt rule into `lib/sampling.py` so the two scripts share one implementation.
4. **`run_config.schema.json`.** Add `ids_waived: boolean` (spec 7.3 research waiver). `secret_scan.py` reads `run_config.ids_waived` or `--ids-waived`. The schema has `additionalProperties: false`, so the key is not schema-valid today, and the flag is the only valid route.
5. **`decisions.csv` columns.** Spec 2.1 names gate, phase, input hash, outcome, reason and time. To connect a G8 reason or a G4 accept to the check it explains, `report.py` accepts an optional `check_id` (or `ref`) column, or an `input_hash` equal to the check's CSV sha256. A decision with neither is matched only for G4, and then to every failed comparison. Proposal: add `check_id` to the decisions writer.
6. **One checks table.** P8 and P9 write rows to `checks/checks_manifest.csv` (columns in `stage2b_common.CHECK_COLUMNS`; the writer upserts by `check_id` and keeps other writers' rows). `run_steps.py` writes `checks/run_steps_checks.csv` (job, check, status, detail) and `reconcile.py` writes `checks/reconcile_status.json` with a `comparisons` list. `report.py` reads all three. Proposal: P6 and P7 also write `checks_manifest.csv` rows, so spec 6.4 has one source.
7. **Manifest seal.** `close.py` treats a manifest as signed only when `manifest.sha256` holds the manifest's current sha256. `report.py` writes it at the end of P11. Proposal: `lib.manifest.seal(run_dir)` and `verify_seal(run_dir)`.
8. **Retention of P8 to P11 files.** `samples/`, `trace/`, `checks/logic_*`, `report.md` and the other P8 to P11 outputs are not added to `manifest.json` `files[]`, because the manifest is owned by stage 2a. As a result `close.py` never deletes them (it fails safe, since unlisted files are never touched). Proposal: before sealing, `report.py` calls `lib.manifest.upsert_files` for every P8 to P11 output.

## Interfaces stage 2a must produce or consume

| File | Producer | Consumer | Shape |
|---|---|---|---|
| `discover_plan.json` | `discover.py plan` | 2a runner | `queries[]` with `query_id`, `rung`, `sql` (GoogleSQL with the `{{project}}`, `{{region}}` and dataset-alias placeholders), `dry_run_first`; `pending[]` |
| `discovery_inputs/<query_id>.json` | 2a runner | `discover.py` | `{"query_id", "status": ok\|denied\|error\|over_cap\|not_run, "rows": [...]}`. Dataset names in rows must be env aliases (D-12). |
| `discovery_terms.json` | discover skill (D1) | `discover.py`, `trap_scan.py` | terms, required_columns, datasets, key_hypotheses, date_columns, period, reference_date, control_table, top_k, stale_days |
| `trace/lookups.json`, `trace/sql/*.sql` | `trace.py generate` | 2a runner (after `compile.py` pin injection; `needs_pin: true`) | one key-bounded, partition-filtered lookup per sampled row and upstream |
| `trace/results/<lookup_id>.json` | 2a runner | `trace.py evaluate` | `{"status": ok\|over_cap\|denied\|error, "rows": [...]}` |
| `checks/logic_inputs` (any run-relative CSV named in the card) | 2a runner | `logic_tests.py` | contract CSV with sidecar: pinned, capped warehouse aggregates |
| `reference_values.csv` | Max or the 2a runner | `logic_tests.py` | grain columns plus `reference_value` |
| compatibility BigQuery results JSON | 2a runner | `compat_suite.py --bigquery-results` | `{fixture_id: {"values": [...], "type": "..."} \| {"error": "..."}}` |
| card front matter | card skill | `card_freeze.py`, `trap_scan.py`, `logic_tests.py`, `report.py` | YAML front matter in `01_question_card.md` and `02_metric_card.md` (fields in `card_freeze.py`) |
| `card_hashes.json` | `card_freeze.py` | `packet_freeze.py` (2a), `logic_tests.py` | `G1a.intent_hash`, `G1b.bindings_hash` |

## Stage 3 status (2026-11-08)

Items 1 to 8 are merged: helpers in `lib/runctx.py` (colliding names: `utc_now_text`, `read_input_json`,
`load_run_config_raw`, `run_cli_args`), `lib/sampling.py`, public `csv_contract.quote_ident` and `canon.type_class`,
`ids_waived` in the schema, `check_id` in `decisions.csv` (written by the new `decide.py`), P6 and P7 rows in
`checks/checks_manifest.csv` (plus `p6_replay`), `lib.manifest.seal` / `verify_seal`, P7 to P11 outputs registered
before the seal. `stage2b_common.py` is deleted. The "2a runner" of the interface table is `warehouse_jobs.py`.

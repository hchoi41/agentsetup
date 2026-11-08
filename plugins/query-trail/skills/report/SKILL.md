---
name: report
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P11 (Report and close): verdict wording by report.py, secrets scan, manifest seal, G16; called only by the query-trail run orchestrator."
user-invocable: false
---

# report (P11)

Purpose: let `report.py` pick the verdict row of spec 4.4 from the independence level, pin status, reference outcome and required checks, and print it with its limitations; then prove no secret left the run. The wording is chosen by code only.

Commands use `QT_PY`, `PLUGIN`, `RUN`, `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P11` — exit 2: do nothing and return.

## 2. Report

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/report.py" --run-dir "$RUN"`

After any fix to the plugin or to a decision row (a scan rerun, a new G8 reason, a superseded gate), run `report.py` again before G16 or `close.py`, so that `report_status.json` and its G16 reasons reflect the current rules; the earlier `report.md` is overwritten and the seal recomputed.

Exit 0 once its inputs exist (the verdict is in the wording, not in the exit code): `report.md` (ending with `## Files`: where the output, the SQL and the gate log are), `report_status.json`, every P7 to P11 output registered in `manifest.json files[]`, and `manifest.sha256` (the run's seal, written by report.py; `close.py` checks it but applies retention only and is not the seal). Exit 2: an input is missing (manifest, checks manifest, logic reference) or the phase check failed; or the evidence is from another attempt: `checks/reconcile_status.json` names another attempt or other steps (`steps_sha256`), or a `checks_manifest.csv` row was written in another attempt (stderr names them). Then rerun the phases named (P7, or P6 to P9) in this attempt; nothing was written. A G8 reason explains a check only in the attempt it was given (or for byte-identical evidence). A P8 trace or P9 logic FAIL explained at G8 gives a qualified verdict ("; sampling: N failures explained at G8 (reasons)", "; logic: <check> failed, explained at G8 (reason)"), never "reconciled" or "verified against reference", and the failing rows stay on the page.

## 3. Secrets scan

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/secret_scan.py" --run-dir "$RUN" --env-file "$ENV_FILE"`

Exit 0 clean; exit 1 a hit (private key, OAuth token, service-account JSON field, an env-file identifier, a project-qualified table name): the run is not complete and nothing is promoted until it is fixed; tell Max the file and line from `secret_scan_report.json` (the matched text is never recorded). Max's own words are not a failure: an identifier inside `00_question.txt`, or on a line of `01_question_card.md` that quotes it (the question is quoted verbatim; a quoted line is exempt from the identifier scan because it matches 00_question.txt word for word, wherever it sits in the card), is reported as an `INFO` line and in `quoted_by_max` (file, line, identifier class); the same identifier anywhere else still fails. The scan also runs when P11 is already done (it writes only `secret_scan_report.json`, outside the seal), so a closed run can be rescanned. For export, `close.py --run-dir "$RUN" --redact-quoted --env-file "$ENV_FILE"` writes `00_question.redacted.txt` and `01_question_card.redacted.md` with placeholders beside the originals, which stay unchanged (they are frozen).

## 4. Close questions

Order at the close: `report.py` → `secret_scan.py` → `view.py` (section 5) → `secret_scan.py` again → G16 → `state.py set --phase P11 --status done` → `secret_scan.py` → `view.py` (the final pair, section 5) → `close.py` (dry run). The G16 row is Max's text in `decisions.csv` and comes after the earlier scans, so without the final pair the closed run's page would say "Not yet secret-scanned".

If `report_status.json` has `g16_needed`, the orchestrator asks G16 with its reasons (no reference, PROVISIONAL pin, a headline claim stronger than the verdict). Declined: the run closes as "not accepted by Max" with his reason.

For a registry entry worth keeping (a verified query, a trap, a rejected table), draft it: `registry.py --run-dir "$RUN" draft --entry F` (exit 3, G6 for Max).

## 5. Run viewer

After the secrets scan is clean (and so after the seal): `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/view.py" --run-dir "$RUN"`. It writes `RUN/view.html`, one page with the step graph, gates, checks and files, and prints `VIEW=<path>`. Exit 2: nothing written; it refuses while `secret_scan_report.json` lists hits in a run file (a hit only in an old `view.html` does not block it: rerun view.py, then the scan). Then run `secret_scan.py` once more: it reads the new page too (its copy of the question is INFO, like the card's). After G16 and `set --phase P11 --status done`, run the final pair: `secret_scan.py`, then `view.py`; the page of the closed run then carries no banner (`run_state.json` and `gates/` are state, not content, and do not bring the banner back; a new `decisions.csv` row does). The page is derived: not in the manifest, not under the seal, never deleted by `close.py`. Until a scan newer than every run file exists the page carries "Not yet secret-scanned: do not share this page"; the verdict on it is labelled "From an earlier pass, not current" when the run has moved back before P11 or the seal no longer matches.

## 6. Tell Max

Start with where things are (Max asked "how can I read the output?" and "where are the SQLs written?" after run 06): first the viewer, `view.html` at the `VIEW=` path ("open this in your browser"), then the first four lines of the `## Files` section at the end of `report.md` (the run folder, the deliverable `outputs/B_whole.csv`, `B.sql` as written, `executed/B.run.sql` as run). The rest of that section (the executed steps, `steps/`, `trace/sql/`, the gate log `decisions.csv`, `trace_report.md`, `13_table_decision.md`, the manifest) stays on the page. The first `report.md` does not list `view.html` (the report is sealed before the viewer exists); when `report.py` runs again after `view.html` exists, `## Files` lists it right after the run folder.

Then quote the verdict line of `report.md` verbatim, with its labels, the independence level, pin status, confidence level and the checks not run at the level. Do not paraphrase, soften or strengthen it. With `reference: none` the limitation reads "No reference was supplied (reference: none at G1a), so accuracy against an outside number was not tested; the verdict is about internal consistency only." (when run_config.json has no `reference` block at all it reads "No reference was declared (reference: none), ..."): it follows from his choice and is not a finding against the run; say it that way. Read the per-unit coverage lines of the sample section with it: what the trace compared against what, and what it did not recompute from source. Sampling is a separate dimension: "; sampling incomplete: ..." or "; sampling: step-to-step only" qualifies the verdict without changing it, and while it stands the report never says "verified" or "traced". "Not reconciled" comes only from a whole-vs-parts FAIL or INCONCLUSIVE, a reference that disagrees and was not accepted at G4, or a required P6, P8 (trace FAIL) or P9 check that failed or did not run without a G8 reason.

Before `close.py`, set P11 to done: `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" set --phase P11 --status done`; `close.py` refuses while P11 is in progress. Retention: `close.py --run-dir "$RUN"` lists what is past `retention_until` (dry run). Deletion is `close.py --apply`, run by Max only; no scheduled task is registered.

## Never

Decide anything or choose wording. Never write "0 differences" alone, never call a run "verified" unless `report.md` says so.

## Artifacts left

`report.md`, `report_status.json`, `manifest.sha256`, `secret_scan_report.json`, `view.html` (derived, unlisted), decisions rows G16 and G6.

## Hands to

G16 and G6 when needed; then the orchestrator ends the run. `decisions.csv` (the gate log) sits outside the manifest seal on purpose: gates such as G16 are appended after report.py seals the run, so sealing it would break the seal on every answer. `close.py` prints the last G16 row with its input_hash beside the sha256 of the current `report.md` ("matches" or "differs from"), so the answer can be tied to the report Max saw.

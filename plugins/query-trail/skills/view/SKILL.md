---
name: view
description: "invoked by Max only as /query-trail:view [run=<run id or folder>]. Renders the run viewer of query-trail: one HTML page with the step graph, gates, checks and files of a run, opened from disk in a browser; prints its path."
disable-model-invocation: true
---

# view: the run viewer (Max only)

Purpose: show Max one run on one page. He opens `view.html` in his browser and sees the step graph, clicks a step to read its sentence, SQL, row counts and checks, and finds every file of the run. The page is derived from the run folder: it decides nothing, changes nothing in the run, and quotes the verdict from `report_status.json` word for word.

Commands use `QT_PY`, `PLUGIN`, `QT_HOME`, `ENV_FILE` as the run skill defines them.

## 1. Which run

- `run=` names an existing folder, or contains `/`, `\` or a drive letter: that folder is `RUN`. Any other value is a run id: `RUN` is `<QT_HOME>/runs/<id>/`.
- No `run=`: the active run (`run_dir` in `<QT_HOME>/active_run.json`); if none is active, the folder under `<QT_HOME>/runs/` whose `run_state.json` was changed last. Say which run you picked.

## 2. Scan, then render

When `RUN/run_state.json` says phase `P11` (pending, in progress or done), first scan: `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/secret_scan.py" --run-dir "$RUN" --env-file "$ENV_FILE"`. It writes only `secret_scan_report.json` (outside the seal), so a closed run can be rescanned; a G16 row added after the last scan otherwise leaves the page saying "Not yet secret-scanned". Exit 1: tell Max the file and line from `secret_scan_report.json` and do not render (view.py would refuse anyway). Before P11 the scan refuses: skip it, and the page carries the banner.

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/view.py" --run-dir "$RUN"`

| Exit | Meaning | Next |
|---|---|---|
| 0 | `RUN/view.html` written; stdout `VIEW=<path>` with forward slashes | tell Max the path and to open it in his browser (double-click, or drag it into Edge or Chrome) |
| 2 | refused, nothing written: not a run folder (`run_config.json` or `run_state.json` missing), `secret_scan_report.json` lists hits in a run file (the page would copy them), or `--out` points into another run | tell Max the reason on stderr; for scan hits, the run must be fixed and `secret_scan.py` rerun first |

## 3. What to tell Max

The path, then one line: the page is a snapshot of the run folder now; rerun `/query-trail:view` after any change. If the page says "Not yet secret-scanned: do not share this page", tell Max that too: it is for his own reading until `secret_scan.py` has run on the run (the report skill does this at P11). If its verdict is labelled "From an earlier pass, not current", say so: the run has moved on since that report. Links on the page open the run's `.sql`, `.csv`, `.json` and `.md` files as text in the browser. Do not summarise the verdict yourself: it is on the page, quoted.

## Never

Edit `view.html` by hand, write anything else into the run, or run any script from this skill other than `secret_scan.py` and `view.py`. `view.html` is not in `manifest.json` and not under the seal; `close.py` never deletes it.

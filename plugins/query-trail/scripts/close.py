"""close.py: retention close by manifest list only (design spec v1 sections 1.4, 7.1, 7.2; decisions Q8).

Lists the manifest's files whose retention_until has passed. retention_until is the manifest's
own value; when a row lacks it, created_utc + run_config retention (6 calendar months by
default, or the G0a override: months or until_date) is used. With --apply it deletes only
manifest-listed files past their date whose sha256 still matches the manifest (a changed file
is skipped and logged) and appends one JSON line per file (path, sha256, retention_until, time,
run id, action) to deletions.log, which is never deleted. It never touches unlisted files,
anything outside the run folder, manifest.json, manifest.sha256, deletions.log, or the
warehouse. Dry run is the default. No scheduled task is registered; Max runs it (criterion F).

The manifest must be sealed: manifest.sha256 (written by report.py at P11) must hold the
manifest's current sha256; a missing or mismatched seal is "unsigned" and exits 2.
Phase: run_state.json must be valid and no phase may be in progress (close runs after P11).

--redact-quoted (fix r6, conductor item 3): 00_question.txt and 01_question_card.md keep Max's words verbatim (the
card skill requires it; secret_scan.py reports identifiers there as quoted_by_max, not as failures). For export this
writes 00_question.redacted.txt and 01_question_card.redacted.md beside them, every env-file identifier replaced by
its placeholder ({{project}}, {{<alias>}}; {{redacted}} for a value with no placeholder), and leaves the frozen
originals untouched. It needs no seal and deletes nothing; the retention listing is skipped.

CLI:  close.py --run-dir R [--apply] [--now 2027-05-08T00:00:00Z]
      close.py --run-dir R --redact-quoted [--env-file F]
Exit: 0 listed or deleted (or redacted copies written); 2 refused (no state, phase in progress, manifest missing or
unsigned; for --redact-quoted, no env file).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import runctx as c2  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib import manifest  # noqa: E402
from lib import state as st  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

LOG = "deletions.log"
QUOTED_FILES = (("00_question.txt", "00_question.redacted.txt"), ("01_question_card.md", "01_question_card.redacted.md"))
NEVER = {"manifest.json", "manifest.sha256", LOG}


def check_state(run_dir: Path) -> st.RunState:
    """A valid run_state.json with no phase in progress (spec 1.5; close runs after P11)."""
    s = st.read_state(st.state_path(run_dir))
    if s.phase_status == "in_progress":
        raise RefusedError(f"{s.phase} is in progress: close runs after P11, nothing ran")
    return s


def check_seal(run_dir: Path) -> str:
    """The manifest sha256, when manifest.sha256 seals it; RefusedError when missing or unsigned (lib.manifest)."""
    return manifest.verify_seal(run_dir)


def retention_until(f: Mapping[str, Any], rc: Mapping[str, Any]) -> dt.datetime:
    """The file's retention date: its manifest value, else created + the run's retention rule (spec 7.1)."""
    if f.get("retention_until"):
        return c2.parse_utc(str(f["retention_until"]))
    ret = rc.get("retention") or {}
    if ret.get("until_date"):
        return c2.parse_utc(str(ret["until_date"]) + "T00:00:00Z")
    return c2.add_months(c2.parse_utc(str(f["created_utc"])), int(ret.get("months", 6)))


def plan(run_dir: Path, now: dt.datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every manifest file with its due status; never a file outside the list."""
    manifest = c2.load_manifest(run_dir)
    rc = c2.load_run_config_raw(run_dir, required=False)
    rows = []
    for f in manifest.get("files", []):
        rel = str(f["path"])
        until = retention_until(f, rc)
        row = {"path": rel, "sha256": f.get("sha256"), "retention_until": until.isoformat().replace("+00:00", "Z")}
        try:
            p = c2.resolve_in_run(run_dir, rel)
        except RefusedError:
            rows.append({**row, "action": "skip: path leaves the run folder"})
            continue
        if p.name in NEVER and p.parent == Path(run_dir).resolve():
            rows.append({**row, "action": "skip: never deleted"})
        elif until > now:
            rows.append({**row, "action": "keep: retention not passed"})
        elif not p.is_file():
            rows.append({**row, "action": "skip: already absent"})
        elif cc.sha256_file(p) != f.get("sha256"):
            rows.append({**row, "action": "skip: sha256 changed since the manifest"})
        else:
            rows.append({**row, "action": "due"})
    return rows, manifest


def close(run_dir: Path, apply: bool, now: dt.datetime) -> list[dict[str, Any]]:
    """List (dry run) or delete the due files; log every applied decision."""
    run_dir = Path(run_dir)
    check_state(run_dir)
    check_seal(run_dir)
    rows, manifest = plan(run_dir, now)
    if not apply:
        return rows
    with open(run_dir / LOG, "ab") as log:
        for r in rows:
            if r["action"] == "due":
                c2.resolve_in_run(run_dir, r["path"]).unlink()
                r["action"] = "deleted"
            if r["action"] in ("deleted", "skip: sha256 changed since the manifest"):
                entry = {"path": r["path"], "sha256": r["sha256"], "retention_until": r["retention_until"],
                         "time_utc": c2.utc_now_text(), "run_id": manifest["run_id"], "action": r["action"]}
                log.write((json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    return rows


def _secret_scan() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("qt_secret_scan_for_close", Path(__file__).with_name("secret_scan.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def redact_quoted(run_dir: Path, env_file: Path | None = None, generic: list[str] | None = None) -> list[str]:
    """Write the placeholder copies of Max's quoted files (fix r6 item 3); return the files written."""
    from lib import envfile

    run_dir = Path(run_dir)
    ss = _secret_scan()
    ef = env_file or ss.default_env_file(c2.load_run_config_raw(run_dir, required=False))
    if ef is None or not Path(ef).is_file():
        raise RefusedError("--redact-quoted needs the env file (--env-file F): its identifiers are what is replaced; "
                           "nothing written")
    env = envfile.load_any(ef)
    names = {v: k for k, v in (env.get("placeholders") or {}).items() if isinstance(v, str)}
    ids = ss.env_file_ids(Path(ef))  # longest first, so a project id is replaced before a shorter dataset id
    written = []
    for src, dst in QUOTED_FILES:
        f = run_dir / src
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        for i in ids:
            if i in text and i not in names and generic is not None and src not in generic:
                generic.append(src)  # fix r8 (r7 LOW 5): no placeholders value matched this identifier
            text = text.replace(i, "{{" + names.get(i, "redacted") + "}}")
        c2.write_text(run_dir / dst, text)
        written.append(dst)
    return written


def g16_line(run_dir: Path) -> str:
    """Fix r9: the last G16 decisions row beside the sha256 of the current report.md (decisions.csv, the gate log, sits
    outside the manifest seal on purpose: gates are appended after report.py seals the run)."""
    import hashlib

    rows = [r for r in c2.load_decisions(run_dir) if str(r.get("gate") or "") == "G16"]
    rp = Path(run_dir) / "report.md"
    rh = hashlib.sha256(rp.read_bytes()).hexdigest() if rp.is_file() else None
    if not rows:
        return f"G16: no decisions row; report.md sha256 {rh or 'absent'}"
    r = rows[-1]
    ih = str(r.get("input_hash") or "")
    match = "matches" if rh and ih == rh else "differs from"
    return (f"G16: {r.get('outcome')} at {r.get('time_utc')} (reason: {r.get('reason') or '-'}); input_hash "
            f"{ih or '-'} {match} report.md sha256 {rh or 'absent'}")


def _main(a: Any) -> int:
    if a.redact_quoted:
        generic: list[str] = []
        written = redact_quoted(a.run_dir, Path(a.env_file) if a.env_file else None, generic)
        for w in written:
            print(f"redacted copy: {w} (the original is unchanged)")
        for g in generic:
            print(f'{g}: an env-file identifier matched no "placeholders" value and was replaced by the generic '
                  '"redacted" placeholder ({{redacted}})')
        if not written:
            print("no 00_question.txt or 01_question_card.md: nothing to redact")
        return int(ExitCode.PASS)
    now = c2.parse_utc(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    rows = close(a.run_dir, a.apply, now)
    print(g16_line(Path(a.run_dir)))
    for r in rows:
        print(f"{r['action']}: {r['path']} (retention_until {r['retention_until']})")
    due = sum(1 for r in rows if r["action"] in ("due", "deleted"))
    print(("deleted " if a.apply else "dry run: would delete ") + f"{due} file(s)")
    return int(ExitCode.PASS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("close.py", "retention close by manifest (spec 7.2)")
    ap.add_argument("--apply", action="store_true", help="delete (default is a dry run)")
    ap.add_argument("--now", help="override the current UTC time (tests, audits)")
    ap.add_argument("--redact-quoted", action="store_true",
                    help="write placeholder copies of 00_question.txt and 01_question_card.md for export; originals kept")
    ap.add_argument("--env-file", help="env file whose identifiers --redact-quoted replaces (default from run_config env)")
    return c2.run_cli_args(_main, ap, argv)


if __name__ == "__main__":
    sys.exit(main())

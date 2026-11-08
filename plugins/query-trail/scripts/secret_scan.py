"""secret_scan.py: the secrets-only exposure gate over a run folder (design spec v1 sections 2.2 P11, 7.3; D-12, D-17 replaced).

Scans every text file under the run folder, line by line, for: private key blocks; Google OAuth
access and refresh tokens; Authorization headers (Bearer, Basic); service-account JSON fields
("type": "service_account", private_key_id, private_key, client_email of
*.iam.gserviceaccount.com); AWS, GitHub, Slack, Anthropic and OpenAI style keys; and
upper-case environment assignments of *TOKEN*, *SECRET*, *PASSWORD*, *API_KEY* with a value
(a Codex run once leaked a session token into its log; project memory 2026-10-11).

Project identifiers (D-12): unless the run's identifiers are waived, the scan also looks for
every string value of the env file %USERPROFILE%\\.query-trail\\env\\<env>.json (or --env-file)
and for backticked three-part `project.dataset.table` names. The waiver is read from
run_config.json `ids_waived: true` (run_config.schema.json since stage 3) or the
--ids-waived flag.

Max's own words (fix r6, conductor item 3): an identifier hit (env_file_identifier, project_qualified_table) inside
00_question.txt, or inside the quoted question of 01_question_card.md (any line, anywhere in the card, whose text
stripped of a leading `> `, list markers, the `question:` key and surrounding whitespace is found word for word in
00_question.txt), is classified (the match is a substring test of that normalised line against the normalised
question text; it is bounded because a qualifying line holds only text Max wrote, so any identifier on it is one he
wrote; an empty line never qualifies, front matter qualifies only under `question:`, headings never do, and the
exemption covers identifier classes only, never secret patterns)
`quoted_by_max`: listed in secret_scan_report.json `quoted_by_max` and printed as an INFO line (file, line, class),
never a failure. The card skill requires that quote verbatim. A secret pattern (key, token) is never exempt, and an
identifier anywhere else still fails. `close.py --redact-quoted` writes placeholder copies of the two files for export.

A hit prints file:line and the pattern id, never the matched text; secret_scan_report.json
records the same.

Fix r15 M2 (cold review of rounds 13 and 14): view.html (view.py's page, at the run root) is scanned like any other
file; it is no longer skipped. A page rendered mid-run can carry text a later scan flags, and such a hit fails the
scan with view.html named. The quoted_by_max rule extends to the page: a line of view.html that, HTML entities decoded,
is found word for word in 00_question.txt (view.py puts every quoted text block on lines of its own) is Max's own
words, INFO. After fixing a hit, rerun view.py (it regenerates the page even when the only hits are in view.html
itself), then this scan. Binary files are skipped and listed.

Fix r15b: runs at P11 whether P11 is pending, in progress or done (a closed run can be rescanned; the scan writes only
secret_scan_report.json, outside the seal). Any other phase is refused (exit 2).

CLI:  secret_scan.py --run-dir R [--env-file F] [--ids-waived]
Exit: 0 clean; 1 a hit (the run is not complete and nothing is promoted until fixed); 2 refused.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import envfile  # noqa: E402
from lib import runctx as c2  # noqa: E402
from lib.exit_codes import CheckFailedError, ExitCode  # noqa: E402

REPORT = "secret_scan_report.json"
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("google_oauth_access_token", re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}")),
    ("google_oauth_refresh_token", re.compile(r"\b1//0[0-9A-Za-z_\-]{20,}")),
    ("authorization_header", re.compile(r"(?i)\bauthorization\b\s*[:=]\s*[\"']?(?:bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{20,}=*")),
    ("service_account_type", re.compile(r"\"type\"\s*:\s*\"service_account\"")),
    ("service_account_private_key_id", re.compile(r"\"private_key_id\"\s*:\s*\"[0-9a-f]{16,}\"")),
    ("service_account_private_key", re.compile(r"\"private_key\"\s*:\s*\"-----BEGIN")),
    ("service_account_email", re.compile(r"[A-Za-z0-9._\-]+@[A-Za-z0-9\-]+\.iam\.gserviceaccount\.com")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github" + "_pat_" + r"[A-Za-z0-9_]{40,})\b")),  # split so the publisher's own gate does not match this source
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_style_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("env_secret_assignment", re.compile(r"\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY)[A-Z0-9_]*"
                                         r"\s*[=:]\s*[\"']?[^\s\"'<>{}]{8,}")),
)
BACKTICK_3PART = re.compile(r"`[a-z][a-z0-9\-]{4,28}[a-z0-9]\.[A-Za-z0-9_]+\.[A-Za-z0-9_$*]+`")
SKIP_NAMES = {REPORT}
VIEW_PAGE = "view.html"  # fix r15 M2: the run viewer at the run root is scanned like any file
IDENTIFIER_CLASSES = {"env_file_identifier", "project_qualified_table"}
QUESTION_TEXT = "00_question.txt"
QUESTION_CARD = "01_question_card.md"
QUOTED = "quoted_by_max"


def env_file_ids(path: Path | None) -> list[str]:
    """Every string value of length >= 4 in the env file (project and dataset IDs, D-12)."""
    if path is None or not path.is_file():
        return []
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str) and len(v) >= 4:
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(envfile.load_any(path))  # JSON or bq_env.sh style (lib.envfile)
    return sorted(set(out), key=len, reverse=True)


def default_env_file(rc: dict[str, Any]) -> Path | None:
    """%USERPROFILE%\\.query-trail\\env\\<env>.json for the run's env name."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if not home or not rc.get("env"):
        return None
    return Path(home) / ".query-trail" / "env" / f"{rc['env']}.json"


def scan_lines(lines: Iterable[str], ids: Sequence[str], scan_ids: bool) -> list[tuple[int, str]]:
    """(line number, pattern id) for every hit."""
    hits = []
    for n, line in enumerate(lines, 1):
        for pid, pat in PATTERNS:
            if pat.search(line):
                hits.append((n, pid))
        if scan_ids:
            if BACKTICK_3PART.search(line):
                hits.append((n, "project_qualified_table"))
            for i in ids:
                if i in line:
                    hits.append((n, "env_file_identifier"))
                    break
    return hits


def _norm(text: str) -> str:
    return " ".join(text.split())


def _quote_body(line: str) -> str:
    """A quoted line's text without its markup: blockquote markers, the `question:` key, YAML quotes and `|`/`>`."""
    t = line.strip()
    while t.startswith(">"):
        t = t[1:].strip()
    t = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", t)
    if t.startswith("question:"):
        t = t[len("question:"):].strip()
    if t in ("|", ">", "|-", ">-"):
        return ""
    return t.strip("\"'").strip()


def quoted_lines(run_dir: Path) -> dict[str, set[int]]:
    """File -> line numbers that are Max's own words: every line of 00_question.txt, and the lines of the card's
    lines, anywhere in the card, whose text (without `> `, list markers, the `question:` key) is found word for word
    in 00_question.txt; no heading is required; fix r15 M2: the lines of view.html (run root) whose text, HTML
    entities decoded, is found word for word in 00_question.txt. Without 00_question.txt nothing is quoted."""
    qf = Path(run_dir) / QUESTION_TEXT
    if not qf.is_file():
        return {}
    qtext = qf.read_text(encoding="utf-8", errors="replace")
    out = {QUESTION_TEXT: set(range(1, len(qtext.splitlines()) + 1))}
    cf = Path(run_dir) / QUESTION_CARD
    words = _norm(qtext)
    lines = cf.read_text(encoding="utf-8", errors="replace").splitlines() if cf.is_file() else []
    marked: set[int] = set()
    in_fm = bool(lines) and lines[0].strip() == "---"
    in_q = False
    for n, line in enumerate(lines, 1):
        if in_fm:
            if n > 1 and line.strip() == "---":
                in_fm = in_q = False
                continue
            if line.startswith("question:"):
                in_q = True
            elif line and not line[0].isspace():
                in_q = False
            cand = in_q
        else:
            cand = not line.lstrip().startswith("#")
        body = _norm(_quote_body(line)) if cand else ""
        if body and body in words:
            marked.add(n)
    if marked:
        out[QUESTION_CARD] = marked
    vp = Path(run_dir) / VIEW_PAGE  # fix r15 M2: the viewer's own copy of the question, line by line
    if vp.is_file():
        page = {n for n, line in enumerate(vp.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                if (b := _norm(_quote_body(html.unescape(line)))) and b in words}
        if page:
            out[VIEW_PAGE] = page
    return out


def scan(run_dir: Path, ids: Sequence[str], scan_ids: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """Scan every file under the run folder; return hits and skipped binary files. An identifier hit on a line that is
    Max's own words carries `class: quoted_by_max` (fix r6 item 3); run() keeps it out of the failures."""
    hits, skipped = [], []
    quoted = quoted_lines(Path(run_dir)) if scan_ids else {}
    for p in sorted(Path(run_dir).rglob("*")):
        if not p.is_file() or p.name in SKIP_NAMES:
            continue
        rel = p.relative_to(run_dir).as_posix()
        data = p.read_bytes()
        if b"\x00" in data[:8192]:
            skipped.append(rel)
            continue
        text = data.decode("utf-8", errors="replace")
        for n, pid in scan_lines(text.splitlines(), ids, scan_ids):
            h = {"file": rel, "line": n, "pattern": pid}
            if pid in IDENTIFIER_CLASSES and n in quoted.get(rel, ()):
                h["class"] = QUOTED
            hits.append(h)
    return hits, skipped


def run(run_dir: Path, env_file: Path | None = None, ids_waived: bool = False) -> tuple[int, dict[str, Any]]:
    """Scan, write the report, return the exit code."""
    run_dir = Path(run_dir)
    rc = c2.load_run_config_raw(run_dir, required=False)
    waived = ids_waived or rc.get("ids_waived") is True
    ef = env_file or default_env_file(rc)
    ids = [] if waived else env_file_ids(ef)
    found, skipped = scan(run_dir, ids, not waived)
    hits = [h for h in found if h.get("class") != QUOTED]
    quoted = [h for h in found if h.get("class") == QUOTED]
    rep = {"scanned_utc": c2.utc_now_text(), "ids_waived": waived, "env_file_ids_checked": len(ids),
           "hits": hits, "quoted_by_max": quoted, "skipped_binary": skipped,
           "note": "matched text is never recorded; quoted_by_max lines are Max's own words (00_question.txt and "
                   "the card's verbatim quote), reported, not failed; close.py --redact-quoted writes export copies"}
    c2.write_json(run_dir / REPORT, rep)
    return (int(ExitCode.FAIL) if hits else int(ExitCode.PASS)), rep


def scan_phase_check(run_dir: Path) -> None:
    """Fix r15b: P11 pending, in progress or done. The scan reads the run and writes only secret_scan_report.json,
    which is outside the seal, so a closed run can be rescanned (the G16 row is appended after the last P11 scan)."""
    c2.check_plugin_version(run_dir)
    from lib import state as st

    s = st.read_state(st.state_path(Path(run_dir)))
    if s.phase != "P11":
        c2.phase_check(run_dir, ["P11"])  # raises with the usual reason


def _main(a: Any) -> int:
    scan_phase_check(a.run_dir)
    code, rep = run(a.run_dir, Path(a.env_file) if a.env_file else None, a.ids_waived)
    for h in rep["quoted_by_max"]:
        print(f"INFO {h['file']}:{h['line']}: {h['pattern']} ({QUOTED}: Max's own words, kept verbatim; "
              "close.py --redact-quoted writes a placeholder copy for export)")
    for h in rep["hits"]:
        sys.stderr.write(f"{h['file']}:{h['line']}: {h['pattern']}\n")
    if code == ExitCode.FAIL:
        raise CheckFailedError(f"{len(rep['hits'])} secret pattern hit(s); the run is not complete until fixed")
    print("no secret pattern found" + (" (identifier scan waived)" if rep["ids_waived"] else ""))
    return code


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("secret_scan.py", "secrets-only exposure gate (spec 7.3)")
    ap.add_argument("--env-file", help="env file whose IDs must not appear (default from run_config env)")
    ap.add_argument("--ids-waived", action="store_true", help="identifier scan waived (run_config ids_waived)")
    return c2.run_cli_args(_main, ap, argv)


if __name__ == "__main__":
    sys.exit(main())

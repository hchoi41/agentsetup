"""csv_check.py: PostToolUse hook on Bash (design spec v1 section 1.5; D-04).

It cannot block (the tool already ran, T14 F6); it reports contract violations of CSVs the command just
wrote in the active run folder (modified in the last 300 s): a BOM, a CR byte, a missing schema sidecar,
or a sidecar whose csv_sha256 differs from the file. The report goes back to the model as
{"decision": "block", "reason": ...} feedback; the CSV stays on disk and the scripts' reader refuses it.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _hooklib as hl  # noqa: E402

WINDOW_S = 300


def problems(csv_path: Path) -> list[str]:
    """Contract problems of one CSV (D-04: UTF-8 without BOM, LF, sidecar with the file's sha256)."""
    data = csv_path.read_bytes()
    out = []
    if data.startswith(b"\xef\xbb\xbf"):
        out.append("BOM")
    if b"\r" in data:
        out.append("CR line ending")
    side = csv_path.with_name(csv_path.name[:-4] + ".schema.json")
    sc = hl.read_json(side)
    if not isinstance(sc, dict):
        out.append("no schema sidecar")
    elif sc.get("csv_sha256") and sc["csv_sha256"] != hashlib.sha256(data).hexdigest():
        out.append("sidecar csv_sha256 differs (file changed after the writer)")
    return out


def main() -> None:
    hl.read_payload()
    run = hl.active_run()
    if run is None:
        sys.exit(0)
    now = time.time()
    found = []
    for p in sorted(run.rglob("*.csv")):
        try:
            if now - p.stat().st_mtime > WINDOW_S:
                continue
            probs = problems(p)
        except OSError:
            continue
        if probs:
            found.append(f"{p.relative_to(run).as_posix()}: {', '.join(probs)}")
    if found:
        sys.stdout.write(json.dumps({"decision": "block", "reason": "query-trail CSV contract (D-04) violated, "
                                     "write CSVs only through the scripts: " + "; ".join(found[:10])}) + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()

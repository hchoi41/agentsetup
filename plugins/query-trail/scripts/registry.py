"""registry.py: the dctx context registry with a hash lock (design spec v1 sections 1.4, 2.1 G6, 2.2 P1 and P11).

Adapted from research_r2/T16_work/dctx.py (lock_snapshot, diff_lock, gate, approve_vq): approved
entries live in <dctx>/registry.json, each entry hashed into <dctx>/registry.lock.json; the D0
gate fails when an approved entry changed after approval, was added without approval, was
removed without review, or carries no approver and date. Draft entries are appended to
<dctx>/drafts.jsonl and shown as unverified hints until Max approves them at G6.

Entry kinds: rejected_table (table, why, use_instead), trap (id, tables, rule), verified_query
(id, question, sql with placeholders only, expected fingerprint), canonical_table (table, grain,
why). SQL and refs must carry placeholders, never a literal project.dataset.table (D-12).

Commands:
  read    (P1)   gate the lock, write <run>/dctx_read.json (approved entries + draft hints); with no registry
                 at all (no registry.json, lock or drafts) it refuses, exit 2, nothing written (fix r2 W12;
                 skills/card: "Exit 2: no registry, continue")
  draft   (P11, also P1) append a draft from --entry FILE; writes gate_request.json for G6, exit 3
  approve (P1 or P11, at G6, Max only) move a draft into the registry and rewrite the lock

CLI:  registry.py --run-dir R [--dctx DIR] read | draft --entry F | approve --id ID --by NAME
Exit: 0 PASS; 1 lock gate failed; 2 refused (also: no registry configured, on read): no warehouse call was
made; local effects: none; 3 G6 needed. Exit 2 wording (fix r3 B3-4, spec v4 1.4): "refused; no warehouse call was made; local effects: <files written, changed or removed, or none>" on stderr (lib/refusal.py).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import runctx as c2  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.exit_codes import CheckFailedError, ExitCode, RefusedError  # noqa: E402

REGISTRY = "registry.json"
LOCK = "registry.lock.json"
DRAFTS = "drafts.jsonl"
KINDS = {"rejected_table": ("table", "why", "use_instead"), "trap": ("tables", "rule"),
         "verified_query": ("question", "sql", "expected"), "canonical_table": ("table", "grain", "why")}
NOT_HUMAN = {"agent", "claude", "model", "codex", "copilot", "assistant"}
THREE_PART = re.compile(r"(?<![\w{`.])([A-Za-z_][\w-]*)\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)(?![\w(])")
PH = re.compile(r"\{\{\s*[A-Za-z_]\w*\s*\}\}")


def literal_ids(text: str) -> list[str]:
    """Literal project.dataset.table references in text (T16 lint_literal_ids)."""
    stripped = PH.sub("PLACEHOLDER", text)
    bad = [m.group(0) for m in THREE_PART.finditer(stripped) if "PLACEHOLDER" not in m.group(0)]
    return bad + [t for t in re.findall(r"`[^`]*\.[^`]*\.[^`]*`", stripped) if "PLACEHOLDER" not in t]


def entry_key(e: Mapping[str, Any]) -> str:
    """kind:id key used in the lock."""
    return f"{e['kind']}:{e['id']}"


def validate_entry(e: Mapping[str, Any]) -> list[str]:
    """Structural problems of one entry (draft or approved)."""
    probs = []
    if e.get("kind") not in KINDS:
        return [f"kind must be one of {sorted(KINDS)}"]
    if not re.fullmatch(r"[a-z0-9_]{1,64}", str(e.get("id", ""))):
        probs.append("id must be 1-64 chars of a-z, 0-9, _")
    for f in KINDS[e["kind"]]:
        if f not in e or e[f] in (None, "", [], {}):
            probs.append(f"{e['kind']} needs {f!r}")
    for f in ("table", "use_instead", "sql"):
        if isinstance(e.get(f), str) and literal_ids(e[f]):
            probs.append(f"{f} carries a literal project.dataset.table; use placeholders (D-12)")
    return probs


def _core(e: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in e.items() if k not in ("approved_by", "approved_utc", "status")}


def lock_snapshot(reg: Mapping[str, Any]) -> dict[str, str]:
    """Per-entry hash of the content Max approved (provenance fields excluded)."""
    return {entry_key(e): c2.sha256_obj(_core(e)) for e in reg.get("entries", [])}


def gate(reg: Mapping[str, Any], lock: Mapping[str, Any]) -> list[str]:
    """D0 gate: approved content must equal the lock, and every entry must carry provenance."""
    cur, locked = lock_snapshot(reg), dict(lock.get("entries", {}))
    probs = [f"changed_after_approval {k}" for k in sorted(cur) if k in locked and cur[k] != locked[k]]
    probs += [f"unapproved_addition {k}" for k in sorted(cur) if k not in locked]
    probs += [f"removed_without_review {k}" for k in sorted(locked) if k not in cur]
    for e in reg.get("entries", []):
        if not (e.get("approved_by") and e.get("approved_utc")):
            probs.append(f"approved_without_provenance {entry_key(e)}")
        probs += [f"invalid {entry_key(e)}: {p}" for p in validate_entry(e)]
    if lock.get("registry_sha256") not in (None, c2.sha256_obj(cur)):
        probs.append("lock registry_sha256 does not match its own entries")
    return probs


def load(dctx: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """registry.json and registry.lock.json (an empty registry when neither exists)."""
    rp, lp = dctx / REGISTRY, dctx / LOCK
    if not rp.exists() and not lp.exists():
        return {"entries": []}, {"entries": {}}
    return c2.read_input_json(rp, REGISTRY), c2.read_input_json(lp, LOCK)


def drafts(dctx: Path) -> list[dict[str, Any]]:
    """Draft entries in append order (later drafts with the same key win)."""
    p = dctx / DRAFTS
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_registry(dctx: Path, reg: dict[str, Any]) -> None:
    """Write registry.json and the matching lock."""
    snap = lock_snapshot(reg)
    c2.write_json(dctx / REGISTRY, reg)
    c2.write_json(dctx / LOCK, {"entries": snap, "registry_sha256": c2.sha256_obj(snap), "locked_utc": c2.utc_now_text()})


def read(run_dir: Path, dctx: Path) -> dict[str, Any]:
    """P1 read: gate the lock (exit 1 on failure); write dctx_read.json for the card skill.

    No registry at all (no registry.json, no lock, no drafts) is a refusal (exit 2, nothing written).
    """
    if not any((dctx / n).exists() for n in (REGISTRY, LOCK, DRAFTS)):
        raise RefusedError(f"no registry configured (no {REGISTRY}, {LOCK} or {DRAFTS} in the dctx folder "
                           f"{dctx.name}); continue without registry context")
    reg, lock = load(dctx)
    probs = gate(reg, lock)
    if probs:
        raise CheckFailedError("dctx lock gate failed: " + "; ".join(probs))
    approved = {entry_key(e) for e in reg["entries"]}
    hints = {entry_key(d): d for d in drafts(dctx) if entry_key(d) not in approved}
    out = {"read_utc": c2.utc_now_text(), "registry_sha256": lock.get("registry_sha256"),
           "approved": reg["entries"], "draft_hints_unverified": list(hints.values())}
    c2.write_json(Path(run_dir) / "dctx_read.json", out)
    return out


def append_draft(run_dir: Path, dctx: Path, entry: dict[str, Any], phase: str) -> int:
    """Append one draft (never edits the registry); G6 is needed to approve it."""
    probs = validate_entry(entry)
    if probs:
        raise RefusedError("draft entry invalid: " + "; ".join(probs))
    d = {**entry, "status": "draft", "drafted_utc": c2.utc_now_text(), "drafted_in_run": Path(run_dir).name,
         "drafted_phase": phase}
    dctx.mkdir(parents=True, exist_ok=True)
    with open(dctx / DRAFTS, "ab") as fh:
        fh.write((json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    c2.write_gate_request(run_dir, "G6", phase, "registry.py", "registry draft awaits approval", entry_key(entry))
    sys.stderr.write(f"NEEDS_DECISION: G6 approval for {entry_key(entry)}\n")
    return int(ExitCode.NEEDS_DECISION)


def approve(dctx: Path, key: str, by: str) -> dict[str, Any]:
    """G6 (Max only): move the latest draft with this key into the registry and relock."""
    if not by.strip() or by.strip().lower() in NOT_HUMAN:
        raise RefusedError("approved_by must be the human approver (G6)")
    reg, lock = load(dctx)
    probs = gate(reg, lock)
    if probs:
        raise CheckFailedError("dctx lock gate failed before approval: " + "; ".join(probs))
    cand = [d for d in drafts(dctx) if entry_key(d) == key or d.get("id") == key]
    if not cand:
        raise RefusedError(f"no draft {key!r}")
    d = {k: v for k, v in cand[-1].items() if k not in ("status", "drafted_utc", "drafted_in_run", "drafted_phase")}
    d.update(approved_by=by.strip(), approved_utc=c2.utc_now_text(), status="approved")
    reg["entries"] = [e for e in reg["entries"] if entry_key(e) != entry_key(d)] + [d]
    write_registry(dctx, reg)
    return d


def _main(a: Any) -> int:
    phases = {"read": ["P1"], "draft": ["P11", "P1"], "approve": ["P1", "P11"]}[a.cmd]
    phase = c2.phase_check(a.run_dir, phases)
    dctx = Path(a.dctx) if a.dctx else Path(a.run_dir).resolve().parent.parent / "dctx"
    if a.cmd == "read":
        out = read(a.run_dir, dctx)
        print(f"{len(out['approved'])} approved entries, {len(out['draft_hints_unverified'])} draft hints")
        return int(ExitCode.PASS)
    if a.cmd == "draft":
        return append_draft(a.run_dir, dctx, c2.read_input_json(Path(a.entry), "draft entry"), phase)
    d = approve(dctx, a.id, a.by)
    print(f"approved {entry_key(d)} by {d['approved_by']}")
    return int(ExitCode.PASS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("registry.py", "dctx registry with hash lock (spec 2.1 G6)")
    ap.add_argument("--dctx", help="registry folder (default: <run-dir>/../../dctx)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("read")
    dr = sub.add_parser("draft")
    dr.add_argument("--entry", required=True, help="JSON file with one entry")
    ap_ = sub.add_parser("approve")
    ap_.add_argument("--id", required=True, help="kind:id or id of the draft")
    ap_.add_argument("--by", required=True, help="Max's name; agents are refused")
    return c2.run_cli_args(lambda a: refusal.guard(a.run_dir, lambda: _main(a)), ap, argv)


if __name__ == "__main__":
    sys.exit(main())

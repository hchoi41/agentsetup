"""decide.py: record Max's answer at a gate in decisions.csv (design spec v1 sections 2.1, 1.4, 1.5, 6.4).

Every gate writes one row to decisions.csv (runctx.DECISION_COLUMNS: gate, phase, input hash, outcome, reason,
time, check_id, decided_by, answer_text, attempt, supersedes, gate_request): the single decisions table DB-09
asks for. decisions.csv is script-owned (the edit guard blocks hand edits), so this is the only writer. The
orchestrator skill asks Max with AskUserQuestion and passes his answer here verbatim (--answer); it never
chooses an outcome itself.

What code enforces (fix brief r0 item 1; the maps are in lib.state):
- the gate must belong to the run state's phase (GATE_PHASES, spec 2.1);
- an open gate_request.json (exit-3 contract) must be answered by its own gate: answering another gate
  is refused. The request is archived to gates/NN_<gate>_request.json with the answer beside it, then
  removed, which is what the Stop hook checks;
- a (gate, phase) that a script opens through gate_request.json (REQUEST_GATES) is answerable only while
  that request is open;
- a gate the orchestrator asks without a request (ORCHESTRATOR_GATES) gets one such row per attempt (and per
  --check-id, e.g. one G3 row per trap); a second needs --supersede, and the row records supersedes=<row n>.
  Rows that answer a request do not count;
- --answer (the human's words) is required and recorded verbatim. --by stays free text: code cannot verify
  who answered, so a faithful answer_text depends on the skill passing it unchanged.
- The input hash is --input-file's sha256 (run-relative), else the open request's sha256, else --input-hash.
- G8 reasons and G4 accepts take --check-id so report.py ties them to the check they explain (NOTES item 5).
- Fix r1 A-H1: G4 applies to reference comparisons only. `--outcome accept` at G4 needs --check-id naming a
  reference check in checks/checks_manifest.csv (family `reference`, e.g. p9_reference) and --category from
  runctx.G4_CATEGORIES (timing, rounding, scope, known_source_defect, other_with_reason); no wildcard. A
  decomposition comparison (B_vs_chain, A_vs_chain) is never accepted at G4: its routes are P5 or a stop with a
  G8 reason. --category is refused on any other gate.
- G12 (conductor ruling r1): when trace/trace_coverage.json leaves deliverable measures not recomputed from
  source, the sheet (trace_report.md) must list them ("Deliverable measures not recomputed from source: ...")
  before G12 can be signed.
- lib.state.GATE_PHASES carries G4 at P7 and P9 (the reference tie-out runs at P9) and G15 at P7 (reconcile.py's
  stream-cap request); decide.py adds no phases of its own.
- G0a also writes run_config.json g0a_approval (approved_by, approved_utc, plan route, ZDR, employer
  approval, decision_row); nothing is probed before it (spec 2.2 P0). Fix r2 A2-M6, value sets (G0A_VALUES):
  --plan-route {system_time, csv_only}, --zdr {yes, no, not_applicable}, --employer-approval {yes, no,
  not_applicable}, --level {60, 70, 80, 90, 100} (run_config.confidence_level and the run state's level),
  --author-mode {single, second:codex, second:max} (run_config.author_mode), --retention-months N (N >= 1:
  run_config.retention.months; it is an override, with override_decision_row, only when N differs from the
  default 6; equal to the default it is recorded as the default). An unknown value is refused (exit 2, nothing
  written) with the allowed list on stderr; these flags belong to G0a only.
- Fix r2 A2-M3: a G4 accept also needs --input-file naming the reference check's CSV (its csv_path in
  checks_manifest.csv); the row's input_hash is that file's sha256 and report.py accepts only on a matching hash.
- Fix r3 A3-4: a G4 or G8 row with --check-id binds to that check's current evidence: input_hash is
  runctx.evidence_sha256 of its row (checks/checks_manifest.csv, else checks/run_steps_checks.csv). Fix r4 C4-1: that
  is always the envelope digest of check_id|attempt|status|detail|<file sha256 or "-">, computed from the current row. A --check-id with no row is refused
  (exit 2, nothing written), and so is an --input-hash that differs from that evidence. runctx.decision_applies
  then applies the row only while the check's evidence is unchanged. (--check-id at other gates, e.g. a G3 trap
  id, is recorded as given.)

- Fix r4 C4-4: G2 approve needs --tables (the tables Max kept); input_hash is lib.g2lock.table_set_sha256 of them
  (sorted placeholder form, LF-joined); --input-file 08_shortlist.csv may be given beside it. discover.py lock refuses
  any other table set. --tables on another gate is refused.
- Fix r4 C4-7: G13 answering discover.py's freshness request needs --check-id freshness:<table>, one of the request's
  check_ids; an `answered` row with that id settles that table's freshness in discover.py build.
- Fix r14 (fix brief r14): outcome `confirmed` exists for one question only: G1b with --check-id
  aggregation_weight:not_applicable, Max confirming that a grouped output (03_output_contract.json grain) wants no
  combined total although the sealed card says not_applicable. card_freeze.py G1b reads that row and passes. Any other
  gate or check-id with `confirmed` is refused (exit 2, nothing written).
- Fix r15 L2: a `confirmed` row's input_hash is runctx.weight_grain_hash of 03_output_contract.json's grain (no
  contract or no grain: exit 2, nothing recorded); card_freeze.py G1b accepts it only for that same grain.

CLI:  decide.py --run-dir R --gate G2 --outcome approve --reason "..." --by NAME --answer "<verbatim>"
                [--tables a,b (G2)]
                [--supersede] [--check-id ID] [--category C (G4 accept)] [--input-file F | --input-hash H]
                [--plan-route R --zdr V --employer-approval V --retention-months N --level L --author-mode M]
                (G0a only; value sets in G0A_VALUES)
Exit: 0 recorded; 2 refused (no state, unknown gate or outcome, gate outside the phase, another gate open,
      no open request for a script-opened gate, a second row without --supersede, empty reason or answer,
      a G4 accept without a reference --check-id, a known --category or the reference CSV as --input-file,
      a G0a flag value outside its set, a G0a flag on another gate, a G4/G8 --check-id with no check row, or
      `confirmed` anywhere but G1b --check-id aggregation_weight:not_applicable).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import runctx as c2  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib import g2lock  # noqa: E402
from lib import state as st  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

GATES = tuple(st.GATE_PHASES)
DEFAULT_RETENTION_MONTHS = 6  # spec 7.1 (runctx.retention_until)
G0A_VALUES: dict[str, tuple[str, ...]] = {  # fix r2 A2-M6 (fix brief r2 shared interface)
    "plan_route": ("system_time", "csv_only"),
    "zdr": ("yes", "no", "not_applicable"),
    "employer_approval": ("yes", "no", "not_applicable"),
    "level": ("60", "70", "80", "90", "100"),
    "author_mode": ("single", "second:codex", "second:max"),
}
OUTCOMES = ("approve", "accept", "reject", "stop", "explained", "waive", "rewrite", "hand_split", "split_finer",
            "filter", "cut_local", "raise_cap", "accept_level_90", "re_pin", "csv_only", "signed", "skip",
            "acknowledge", "decline", "not_applicable", "answered", "change_course", "confirmed")
CONFIRMED_ONLY = ("G1b", "aggregation_weight:not_applicable")  # fix r14: the one (gate, check_id) that takes `confirmed`


def check_g12(run_dir: Path) -> None:
    """The G12 sheet must list the deliverable measures not recomputed from source (conductor ruling r1)."""
    cp = run_dir / "trace" / "trace_coverage.json"
    if not cp.is_file():
        return
    cov = c2.read_input_json(cp, "trace/trace_coverage.json")
    src = set(cov.get("measures_from_source") or [])
    missing = [m for m in cov.get("deliverable_measures") or [] if m not in src]
    if not missing:
        return
    sheet = run_dir / "trace_report.md"
    text = sheet.read_text(encoding="utf-8") if sheet.is_file() else ""
    if f"Deliverable measures not recomputed from source: {', '.join(missing)}." not in text:
        raise RefusedError("G12: trace_report.md does not list the deliverable measures not recomputed from source ("
                           + ", ".join(missing) + "); run trace.py evaluate, then sign")


def check_g0a_values(g0a: dict[str, Any] | None) -> None:
    """Fix r2 A2-M6: refuse (exit 2) a G0a flag value outside its set, naming the allowed values."""
    for k, v in (g0a or {}).items():
        if v is None:
            continue
        flag = "--" + k.replace("_", "-")
        if k == "retention_months":
            try:
                ok = int(str(v)) >= 1
            except ValueError:
                ok = False
            if not ok:
                raise RefusedError(f"{flag} {v!r}: allowed: a whole number of months, 1 or more "
                                   f"(the default is {DEFAULT_RETENTION_MONTHS})")
        elif str(v) not in G0A_VALUES[k]:
            raise RefusedError(f"{flag} {v!r}: allowed: {', '.join(G0A_VALUES[k])}")


def check_g4(run_dir: Path, outcome: str, check_id: str | None, category: str | None,
             input_file: str | None = None) -> None:
    """Fix r1 A-H1: a G4 accept names a reference check and a category; refuse anything else (exit 2).

    Fix r2 A2-M3: and the reference comparison CSV as --input-file (the check's csv_path), so a later reference
    result with another hash is not covered by this accept.
    """
    if outcome not in ("accept",):
        return
    if not check_id:
        raise RefusedError("G4 accept needs --check-id naming the reference comparison (e.g. p9_reference): "
                           "no wildcard accept")
    rows = {str(r.get("check_id")): r for r in c2.load_checks(run_dir)}
    row = rows.get(check_id)
    if row is None or str(row.get("family")) != "reference":
        raise RefusedError(f"G4 applies to reference comparisons only; {check_id!r} is not a reference check in "
                           "checks/checks_manifest.csv. A B vs chain(B) or A vs chain(B) difference goes back to P5 "
                           "(re-split or rewrite) or stops with a G8 reason")
    if category not in c2.G4_CATEGORIES:
        raise RefusedError(f"G4 accept needs --category, one of {', '.join(c2.G4_CATEGORIES)}")
    rel = row.get("csv_path")
    if not input_file:
        raise RefusedError(f"G4 accept needs --input-file naming the reference comparison CSV ({rel or 'its CSV'}): "
                           "the accept is tied to that file's hash")
    if not rel or c2.resolve_in_run(run_dir, input_file) != c2.resolve_in_run(run_dir, str(rel)):
        raise RefusedError(f"G4 accept: --input-file must be {check_id}'s comparison CSV ({rel or 'none recorded'}), "
                           f"not {input_file}")
    f = c2.resolve_in_run(run_dir, str(rel))
    if not f.is_file():
        raise RefusedError(f"G4 accept: {rel} does not exist")
    if row.get("csv_sha256") and cc.sha256_file(f) != str(row["csv_sha256"]):
        raise RefusedError(f"G4 accept: {rel} changed since {check_id} was written (sha256 differs): rerun P9")


EVIDENCE_GATES = ("G4", "G8")  # their rows explain checks (runctx._explains), so they bind to the check's evidence


def check_evidence(run_dir: Path, gate: str, check_id: str, input_hash: str | None = None) -> str:
    """Fix r3 A3-4: the evidence_sha256 of `check_id`'s current row; refuse (exit 2) when there is no row or when an
    --input-hash names other evidence."""
    row = c2.find_check(run_dir, check_id)
    if row is None:
        raise RefusedError(f"{gate} --check-id {check_id!r}: no such check in checks/checks_manifest.csv or "
                           "checks/run_steps_checks.csv; a decision explains a check that has a result row: "
                           "nothing recorded")
    h = c2.evidence_sha256(row)
    if input_hash and str(input_hash).strip() != h:
        raise RefusedError(f"{gate} --input-hash {input_hash} is not {check_id}'s current evidence ({h}): the check "
                           "changed since; nothing recorded")
    return h


def check_g13(req: dict[str, Any] | None, check_id: str | None) -> None:
    """Fix r4 C4-7: a G13 answer to discover.py's freshness request names one table: --check-id freshness:<table>,
    one of the request's check_ids."""
    if not req or str(req.get("gate")) != "G13":
        return
    ids = [str(x) for x in (req.get("details") or {}).get("check_ids") or []] if isinstance(req.get("details"), dict)         else []
    if ids and check_id not in ids:
        raise RefusedError(f"G13 needs --check-id naming the table it answers, one of: {', '.join(ids)}")


def g13_entry_hash(req: Mapping[str, Any], check_id: str) -> str | None:
    """Fix r5 (cold reviewer LOW): the input_hash discover.py gave the request entry of one table (its freshness
    numbers); a G13 answer row records it, so the answer settles that table only while the numbers are unchanged."""
    det = req.get("details") if isinstance(req.get("details"), dict) else {}
    for t in det.get("tables") or []:
        if isinstance(t, Mapping) and str(t.get("check_id")) == check_id and t.get("input_hash"):
            return str(t["input_hash"])
    return None


def confirmed_grain_hash(run_dir: Path) -> str:
    """Fix r15 L2: input_hash of a G1b `confirmed` row: runctx.weight_grain_hash of 03_output_contract.json's grain.
    No contract, or an ungrouped one, leaves nothing to confirm (exit 2, nothing recorded)."""
    p = Path(run_dir) / "03_output_contract.json"
    grain = c2.contract_grain(c2.read_input_json(p, "03_output_contract.json"))
    if not grain:
        raise RefusedError("03_output_contract.json has no grain: the output is not grouped, so there is no "
                           "aggregation_weight to confirm; nothing recorded")
    return c2.weight_grain_hash(grain)


def _open_request(run_dir: Path) -> dict[str, Any] | None:
    p = run_dir / c2.GATE_REQUEST
    return c2.read_input_json(p, "gate_request.json") if p.is_file() else None


def _supersedes(run_dir: Path, gate: str, attempt: int, supersede: bool, check_id: str | None) -> int | None:
    """Once-per-attempt rule for orchestrator gates (per check_id: a G3 trap resolution is its own row);
    return the 1-based row a --supersede row replaces."""
    rows = c2.load_decisions(run_dir)
    prior = [i for i, r in enumerate(rows, 1) if str(r.get("gate")) == gate and int(r.get("attempt") or 1) == attempt
             and not r.get("gate_request") and (r.get("check_id") or None) == (check_id or None)]
    what = f"{gate}{f' (check {check_id})' if check_id else ''}"
    if prior and not supersede:
        raise RefusedError(f"{what} already has a row in attempt {attempt} (decisions.csv row {prior[-1]}); "
                           "a new answer needs --supersede")
    if supersede and not prior:
        raise RefusedError(f"--supersede: {what} has no row in attempt {attempt} to supersede")
    return prior[-1] if prior else None


def record(run_dir: Path, gate: str, outcome: str, reason: str, by: str, check_id: str | None = None,
           input_file: str | None = None, input_hash: str | None = None, g0a: dict[str, Any] | None = None,
           answer: str | None = None, supersede: bool = False, category: str | None = None,
           tables: str | None = None) -> dict[str, Any]:
    """Append the decision; archive and clear a matching open request; return the row."""
    c2.check_plugin_version(run_dir)  # fix r4 C4-LOW: a run started under another plugin version is a new run
    s = st.read_state(st.state_path(run_dir))
    if gate not in GATES:
        raise RefusedError(f"unknown gate {gate!r} (spec 2.1: {', '.join(GATES)})")
    if outcome not in OUTCOMES:
        raise RefusedError(f"unknown outcome {outcome!r}: one of {', '.join(OUTCOMES)}")
    if not reason.strip() or not by.strip():
        raise RefusedError("a decision needs a reason and the name of who decided")
    if answer is None or not answer.strip():
        raise RefusedError("a decision needs --answer: the human's answer verbatim, as the skill received it")
    if outcome == "confirmed" and (gate, (check_id or "").strip()) != CONFIRMED_ONLY:
        raise RefusedError(f"outcome confirmed belongs to {CONFIRMED_ONLY[0]} --check-id {CONFIRMED_ONLY[1]} only "
                           "(Max confirming that a grouped output wants no combined total): nothing recorded")
    grain_hash = confirmed_grain_hash(run_dir) if outcome == "confirmed" else None
    if s.phase not in st.GATE_PHASES[gate]:
        raise RefusedError(f"{gate} belongs to {', '.join(st.GATE_PHASES[gate])}; the run state is at {s.phase} "
                           "(spec 2.1): nothing recorded")
    if category is not None and gate != "G4":
        raise RefusedError("--category applies to G4 accepts only")
    if gate != "G0a" and any(v is not None for v in (g0a or {}).values()):
        raise RefusedError("--plan-route, --zdr, --employer-approval, --retention-months, --level and --author-mode "
                           "belong to G0a only")
    check_g0a_values(g0a)
    if tables is not None and gate != "G2":
        raise RefusedError("--tables belongs to G2 only (the table set Max kept)")
    table_list = g2lock.split_tables(tables) if tables is not None else None
    if gate == "G2" and outcome == "approve" and not table_list:
        raise RefusedError("G2 approve needs --tables: the comma-separated tables Max kept (the lock binds to them)")
    if gate == "G2" and table_list:
        table_hash = g2lock.table_set_sha256(table_list)  # refuses a ref that is not <alias>.<table>
    if gate == "G4":
        check_g4(run_dir, outcome, check_id, category, input_file)
    if gate == "G12" and outcome in ("signed", "approve", "accept"):
        check_g12(run_dir)
    req = _open_request(run_dir)
    if req and str(req.get("gate")) != gate:
        raise RefusedError(f"gate {req.get('gate')} is open (gate_request.json); answer it before {gate}")
    if not req and (gate, s.phase) in st.REQUEST_GATES:
        raise RefusedError(f"{gate} at {s.phase} is opened by a script (gate_request.json, exit 3); none is open, "
                           "so there is nothing to answer")
    if gate == "G13":
        check_g13(req, check_id)
    supersedes = None
    if req:
        if supersede:
            raise RefusedError("--supersede does not apply to the answer of an open gate_request.json")
    elif gate in st.ORCHESTRATOR_GATES:
        supersedes = _supersedes(run_dir, gate, s.attempt, supersede, check_id)
    elif supersede:
        raise RefusedError("--supersede applies only to once-per-attempt gates: "
                           + ", ".join(sorted(st.ORCHESTRATOR_GATES)))
    if gate == "G2" and table_list:
        if input_file:
            c2.resolve_in_run(run_dir, input_file)  # the shortlist he saw; the hash is the table set (fix r4 C4-4)
        h = table_hash
    elif check_id and gate in EVIDENCE_GATES:
        h = check_evidence(run_dir, gate, check_id, input_hash)
    elif gate == "G13" and req and check_id and g13_entry_hash(req, check_id):
        h = g13_entry_hash(req, check_id)  # fix r5: bound to that table's freshness numbers
    elif input_file:
        h = cc.sha256_file(c2.resolve_in_run(run_dir, input_file))
    elif req:
        h = cc.sha256_file(run_dir / c2.GATE_REQUEST)
    else:
        h = input_hash
    if grain_hash is not None:
        h = grain_hash  # fix r15 L2: the confirmation is bound to the grain it was given for
    n = len(c2.load_decisions(run_dir)) + 1
    archived = f"gates/{n:02d}_{gate}_request.json" if req else None
    row = c2.append_decision(run_dir, gate, s.phase, outcome, reason.strip(), h, check_id, by.strip(),
                             answer_text=answer, attempt=s.attempt, supersedes=supersedes, gate_request=archived,
                             category=category)
    if req:
        gdir = run_dir / "gates"
        gdir.mkdir(exist_ok=True)
        c2.write_json(gdir / f"{n:02d}_{gate}_request.json", req)
        c2.write_json(gdir / f"{n:02d}_{gate}_answer.json", row)
        (run_dir / c2.GATE_REQUEST).unlink()
    if gate == "G0a" and outcome == "approve":
        cfg = c2.load_run_config_raw(run_dir)
        block = {"approved_by": by.strip(), "approved_utc": row["time_utc"], "decision_row": f"decisions.csv#{n}"}
        for k, v in (g0a or {}).items():
            if v is not None:
                block[k] = v
        retention = block.pop("retention_months", None)
        level = block.pop("level", None)
        author_mode = block.pop("author_mode", None)
        cfg["g0a_approval"] = block
        if retention is not None:
            months = int(retention)
            cfg["retention"] = ({"months": months} if months == DEFAULT_RETENTION_MONTHS else
                                {"months": months, "override_decision_row": f"decisions.csv#{n}"})
            print(f"retention: {'default' if months == DEFAULT_RETENTION_MONTHS else 'override'} ({months} months)")
        if level is not None:
            cfg["confidence_level"] = int(level)
        if author_mode is not None:
            cfg["author_mode"] = author_mode
        c2.write_json(run_dir / c2.RUN_CONFIG, cfg)
        c2.load_run_config(run_dir)  # refuse (exit 2) if the block made run_config.json schema-invalid
        if level is not None and int(level) != s.level:
            st.write_state(st.state_path(run_dir), st.update_state(s, level=int(level)))
    return row


def _main(a: Any) -> int:
    g0a = {"plan_route": a.plan_route, "zdr": a.zdr, "employer_approval": a.employer_approval,
           "retention_months": a.retention_months, "level": a.level, "author_mode": a.author_mode}
    row = record(a.run_dir, a.gate, a.outcome, a.reason, a.by, a.check_id, a.input_file, a.input_hash, g0a,
                 a.answer, a.supersede, a.category, a.tables)
    print(f"decisions.csv: {row['gate']} {row['outcome']} recorded ({row['time_utc']})")
    return int(ExitCode.PASS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("decide.py", "record a gate decision (spec 2.1)")
    ap.add_argument("--gate", required=True)
    ap.add_argument("--outcome", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--by", required=True, help="who decided (Max, or who acted on his recorded instruction)")
    ap.add_argument("--answer", required=True, help="the human's answer verbatim, as the skill received it")
    ap.add_argument("--supersede", action="store_true", help="replace this attempt's row of a once-per-attempt gate")
    ap.add_argument("--check-id", default=None)
    ap.add_argument("--category", default=None, help="G4 accept only: " + ", ".join(c2.G4_CATEGORIES))
    ap.add_argument("--input-file", default=None, help="run-relative file the decision is about")
    ap.add_argument("--input-hash", default=None)
    ap.add_argument("--tables", default=None, help="G2 only: comma-separated tables Max kept; input_hash = their "
                                                    "table-set sha256 (lib.g2lock)")
    ap.add_argument("--plan-route", default=None, help="G0a only: " + ", ".join(G0A_VALUES["plan_route"]))
    ap.add_argument("--zdr", default=None, help="G0a only: " + ", ".join(G0A_VALUES["zdr"]))
    ap.add_argument("--employer-approval", default=None, help="G0a only: " + ", ".join(G0A_VALUES["employer_approval"]))
    ap.add_argument("--retention-months", default=None,
                    help=f"G0a only: months >= 1; an override only when not {DEFAULT_RETENTION_MONTHS}")
    ap.add_argument("--level", default=None, help="G0a only: default level, " + ", ".join(G0A_VALUES["level"]))
    ap.add_argument("--author-mode", default=None, help="G0a only: " + ", ".join(G0A_VALUES["author_mode"]))
    return c2.run_cli_args(_main, ap, argv)


if __name__ == "__main__":
    sys.exit(main())

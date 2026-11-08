"""report.py: P11 report, the only place wording is chosen (design spec v1 sections 2.2 P11, 2.3, 4.3, 4.4, 6.4, 7.1).

Reads manifest.json, run_state.json, run_config.json, the metric card, decisions.csv,
checks/checks_manifest.csv (P8 and P9 rows), checks/run_steps_checks.csv (run_steps.py's P6 step
checks, job/check/status/detail), checks/reconcile_status.json (reconcile.py's P7 `comparisons`
list), the P9 reference outcome (checks/logic_reference.json), the P8 trace results and the P8 trace
coverage (trace/trace_coverage.json), then:

1. classifies every check by spec 6.4 (PASS, FAIL-explained, FAIL-unexplained,
   NOT-RUN-at-level; runctx.classify_checks, shared with logic_tests.py); a required P6 or P9 check that
   did not run although it could (a P9 test the card left out) is unexplained until a G8 reason names it
   (fix r1 A-M4). Sampling (p8_trace) is its own assurance dimension (conductor ruling r1): a p8_trace
   that is NOT_RUN because coverage is incomplete, or missing, never flips the verdict; it adds the
   qualifier "; sampling incomplete: <measures> not recomputed from source" ("; sampling: step-to-step
   only" when nothing was recomputed from a source table; "; sampling not run" without a p8_trace row)
   and blocks the words "verified" and "traced" anywhere in report.md. A p8_trace FAIL that is not
   explained still gives "not reconciled" (spec 2.3, "trace not reproduced");
2. picks the first matching row of the spec 4.4 verdict table from independence level, pin
   status, reference outcome, the comparison and the required checks. Fix r1:
   - a B_vs_chain or A_vs_chain FAIL is never explained, at G4 or G8 (A-H1): "not reconciled:
     decomposition differs (classes)"; its routes are P5 or a stop;
   - an INCONCLUSIVE comparison is never positive (A-H2): "not reconciled (comparison inconclusive:
     reason)"; any other non-PASS comparison counts as unexplained;
   - G4 accepts a reference difference only, named by check_id p9_reference with a category: the
     wording carries "; reference differs: N rows accepted at G4 (categories)", never "agrees";
   - the reference suffix names its independence (A-M1): "; reference agrees within tolerance" for an
     IND-5 reference only, "; synthetic reference agrees (demonstration, not evidence)",
     "; same-source reference agrees (not independent)";
   - the sampling qualifier above (A-H5, conductor ruling r1); with it, row 6 reads "reconciled;
     reference agrees within tolerance", never "verified against reference";
3. applies the spec 2.3 wording effects: a PROVISIONAL pin is printed with its reasons and gives
   "reconciled (provisional)" where the table says so; degraded steps, hand splits (G10) and lint
   waivers (G9) are listed; owner mode is printed; explained exceptions are counted ("with N
   explained exceptions"); "0 differences" never appears alone;
4. writes report.md (verdict, budget spent / budget, comparison method and checks not run per
   comparison, headline table, required checks, per-step rows in -> rows out with reason, sample
   sentences with per-unit trace coverage, checks not run, limitations, retention dates),
   report_status.json (verdict row, G16 need and reasons) and manifest.sha256, the seal close.py
   requires.

Fix r2:
- A2-H1: refuses (exit 2, nothing written) a checks/reconcile_status.json whose `attempt` is not the run state's or
  whose `steps_sha256` is not runctx.steps_sha256(manifest.json) (or that has neither), and any checks_manifest.csv
  row of another attempt; decisions explain, accept or list only when runctx.decision_applies (fix r3 A3-4: the
  row's input_hash equals the check's evidence_sha256, nothing else);
- A2-H4: a P8 trace or P9 logic FAIL explained at G8 caps a positive verdict at "consistent" (never "verified
  against reference", never "reconciled") and appends "; sampling: N failures explained at G8 (reasons)" or
  "; logic: <check> failed, explained at G8 (reason)"; the failing rows are listed under "Explained numerical
  failures (G8)";
- A2-M1: "(source recompute failed: <measures>)" when trace_coverage.json lists measures_source_failed;
- A2-M3: a G4 accept counts only when its input_hash is the reference comparison CSV's sha256;
- dec L2: no trace/trace_coverage.json means sampling incomplete; dec L6: quoted provenance and check details are
  inline-code spans that the word block leaves verbatim.

Fix r5 M1: completeness is computed here per required (unit, measure) pair from the coverage units (a unit's
`measures_from_source`, else its measures minus `measures_not_from_source`), never taken from the run-level list
alone: a measure is complete only when every unit that carries it has it from source (and the run-level list, when
present, agrees). A measure complete in some units only keeps the qualifier, which names it "(partially recomputed:
<measure> in k of n units)", so "verified" is not eligible.

Fix r3:
- A3-4: the stale refusal also covers checks/run_steps_checks.csv through its `attempt` column (a row without one
  counts as attempt 1); its P6 rows keep their own evidence_sha256 (runctx.steps_check_rows).

Fix r11 (live run 06):
- L5: report.md ends with `## Files`: the run folder (absolute, forward slashes), then one line each, relative to it
  and only when the file exists: outputs/B_whole.csv (the deliverable), B.sql (as written), executed/B.run.sql and
  the executed steps (as run, pin applied), steps/, trace/sql/, decisions.csv (the gate log), trace_report.md,
  13_table_decision.md, manifest.json + manifest.sha256 (the seal this same call writes). The report skill quotes
  the section's first four lines in the close message.
- L6: with `reference: none` in run_config.json the accuracy sentence (limitation and the row-5/7 extra) reads
  NO_REFERENCE: a consequence of Max's choice, not a finding. A declared reference that was not compared keeps
  ACCURACY.

Fix r12 (cold review of round 11):
- L3: the `## Files` step range is built from the numbered executed/NN.run.sql files only; executed/A.run.sql
  (second-author mode) gets its own line when it exists.
- L4: with no `reference` block in run_config.json at all the sentence is NO_REFERENCE_DECLARED ("No reference was
  declared (reference: none) ..."); NO_REFERENCE ("... reference: none at G1a ...") only when the block exists with
  type none.

Round 13 (run viewer): when view.html exists, `## Files` lists it right after the run folder ("open it in a browser
(regenerate with view.py)"). view.html is never registered in manifest.json (late_outputs does not list it).

CLI:  report.py --run-dir R
Exit: 0 always once the inputs exist (the verdict is in the wording, not the exit code);
      2 when an input is missing, the phase check fails, or the evidence is from another attempt or other steps.
"""
from __future__ import annotations

import decimal
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import canon  # noqa: E402
from lib import runctx as c2  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib import manifest as mf  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402
from lib.levels import load_ladder  # noqa: E402

STEP_RUN = re.compile(r"\d+\.run\.sql")  # fix r12 L3: executed/NN.run.sql; B.run.sql and A.run.sql are not steps
WEAK = {"IND-0", "IND-1", "IND-2"}
STRONG = {"IND-3", "IND-4", "IND-5"}
ACCURACY = "No independent reference value was compared; accuracy is not established."
NO_REFERENCE = ("No reference was supplied (reference: none at G1a), so accuracy against an outside number was not "
                "tested; the verdict is about internal consistency only.")
NO_REFERENCE_DECLARED = ("No reference was declared (reference: none), so accuracy against an outside number was not "
                         "tested; the verdict is about internal consistency only.")  # fix r12 L4: no reference block
NOT_INDEPENDENT = "\"Verified\" needs an independent reference (IND-5, spec 4.3); this reference is not independent."
# The keys are claim vocabulary (card_freeze accepts every key as a headline.claim word); strength() ranks a wording
# by its leading word, so qualified wordings need no key of their own (fix r9 dropped the two dead qualified keys).
STRENGTH = {"not reconciled": 0, "consistent": 2, "reconciled (provisional)": 3, "reconciled": 4, "verified against reference": 5}
REF_SUFFIX = {"independent": "; reference agrees within tolerance",
              "synthetic": "; synthetic reference agrees (demonstration, not evidence)",
              "same_source": "; same-source reference agrees (not independent)"}
STEP_TO_STEP = "; sampling: step-to-step only"
WITHHELD = "[withheld: sampling incomplete]"
DEFAULT_FLOAT_SCALE = 4  # headline FLOAT columns without a contract scale (fix r1 A-LOW default)
TRACE_COVERAGE = "trace/trace_coverage.json"


# ---------------------------------------------------------------- verdict (spec 4.4)
def reference_kind(ref_independence: str | None, ref_type: str | None = None) -> str:
    """independent (IND-5: GL trial balance or control total), synthetic, or same_source (anything else)."""
    if ref_type == "synthetic" or ref_independence == "synthetic":
        return "synthetic"
    if ref_independence == "independent" and ref_type not in ("bi_dashboard",):
        return "independent"
    return "same_source"


def verdict(ind: str, pin: str, reference: str, ref_independence: str | None, unexplained: bool, *,
            ref_type: str | None = None, comparison: str | None = None, ref_accepted: str | None = None
            ) -> tuple[int, str, list[str]]:
    """Spec 4.4, top to bottom, first match wins: (row number, wording, extra sentences).

    `comparison` is the row-1 wording of a failed or inconclusive comparison; `ref_accepted` the suffix of a
    reference difference accepted at G4 (then the reference row 2 does not apply).
    """
    if comparison:
        return 1, comparison, []
    if unexplained:
        return 1, "not reconciled", []
    if reference == "disagree" and not ref_accepted:
        return 2, "not reconciled (to reference)", []
    kind = reference_kind(ref_independence, ref_type)
    agree = reference == "agree"
    suffix = REF_SUFFIX[kind] if agree else (ref_accepted or "")
    if ind in WEAK:
        base = "consistent (decomposition check)" if ind == "IND-0" else "consistent"
        return (4, base + suffix, []) if suffix else (3, base, [])
    if pin == "FINAL":
        if agree:
            if kind == "independent":
                return 6, "verified against reference", []
            return 6, "reconciled" + suffix, [NOT_INDEPENDENT]
        return 5, "reconciled" + suffix, [ACCURACY]
    if agree and kind == "independent":
        return 7, "reconciled (provisional)", []
    return 7, "reconciled (provisional)" + suffix, [ACCURACY]


LOWERING_QUALIFIERS = ("sampling incomplete", "not reconciled")


def strength(wording: Any) -> int:
    """Fix r6 (conductor item 4): the strength of a verdict or a headline claim, by its leading verdict word.

    The word before the first "(" or ";" is ranked with STRENGTH; qualifiers in parentheses or after ";" do not lower
    it ("consistent (decomposition check)" is as strong as "consistent"), except "sampling incomplete" and "not
    reconciled", which make it 0, and "provisional" (a PROVISIONAL pin), which ranks it one step below its FINAL
    form (conductor ruling, round 6: "reconciled (provisional)" < "reconciled"). An unknown word (or `none`) is 0."""
    w = str(wording or "").strip().lower()
    head = re.split(r"[(;]", w, maxsplit=1)[0].strip()
    rest = w[len(head):]
    if any(q in rest for q in LOWERING_QUALIFIERS) or "sampling incomplete" in w:
        return 0
    base = STRENGTH.get(head, 0)
    return max(base - 1, 0) if "provisional" in rest else base


def is_provisional(wording: Any) -> bool:
    """A verdict carrying a PROVISIONAL pin."""
    return "provisional" in str(wording or "").lower()


def headline_unsupported(claim: Any, wording: str) -> bool:
    """G16 fires only when the card's headline claim is stronger than the verdict (fix r6 item 4)."""
    return bool(claim) and strength(claim) > strength(wording)


# ---------------------------------------------------------------- P7 comparison
def comparison_status(run_dir: Path, checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """P7 outcome: status PASS, FAIL, INCONCLUSIVE or NOT-PASS, the row-1 wording when not PASS, the comparisons.

    No decision explains a comparison (fix r1 A-H1): a decomposition FAIL routes to P5 or a stop.
    """
    p = run_dir / "checks" / "reconcile_status.json"
    if p.is_file():
        s = c2.read_input_json(p, "checks/reconcile_status.json")
        comps = s.get("comparisons") or [{"comparison": s.get("comparison", "B vs chain(B)"), "status": s.get("status"),
                                          "classes": s.get("classes") or []}]
        fails = [c for c in comps if str(c.get("status")) == "FAIL"]
        inconc = [c for c in comps if str(c.get("status")) == "INCONCLUSIVE"]
        out = {"comparisons": comps, "adequacy_not_run": list(s.get("adequacy_counters_not_run") or []),
               "status": "PASS", "wording": None}
        if fails:
            classes = sorted({str(x) for c in fails for x in (c.get("classes") or [])})
            out.update(status="FAIL", wording="not reconciled: decomposition differs (" +
                       (", ".join(classes) or "unclassified") + ")")
        elif inconc:
            reason = "; ".join(str(c.get("inconclusive_reason") or c.get("method") or "nothing compared") for c in inconc)
            out.update(status="INCONCLUSIVE", wording=f"not reconciled (comparison inconclusive: {reason})")
        elif any(str(c.get("status")) != "PASS" for c in comps):
            out["status"] = "NOT-PASS"
        return out
    p7 = [c for c in checks if str(c.get("phase")) == "P7"]
    if not p7:
        raise c2.InputMissingError("no P7 comparison status (checks/reconcile_status.json or P7 checks_manifest rows)")
    comps = [{"comparison": c["check_id"], "status": c.get("status"), "classes": [], "method": "P7 checks_manifest row"}
             for c in p7]
    return {"comparisons": comps, "adequacy_not_run": [], "wording": None,
            "status": "PASS" if all(c.get("status") == "PASS" for c in p7) else "NOT-PASS"}


def reference_acceptance(run_dir: Path, ref: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]]) -> str | None:
    """Suffix for a reference difference accepted at G4 (check_id p9_reference, a category, a reason), else None.

    Fix r2 A2-M3: the accept's input_hash must be the reference comparison CSV's sha256 (decide.py records it from
    --input-file); an accept with no hash, or with another result's hash, accepts nothing.
    """
    if str(ref.get("outcome")) != "disagree":
        return None
    n, hashes = None, set()
    rel = ref.get("csv")
    if rel:
        f = c2.resolve_in_run(run_dir, str(rel))
        if f.is_file():
            n = sum(1 for r in c2.read_records(f)[1] if str(r.get("status")) == "FAIL")
            hashes = {cc.sha256_file(f), cc.read_sidecar(f).csv_sha256}
    cats = []
    for d in decisions:
        if str(d.get("gate")).upper() != "G4" or str(d.get("outcome", "")).lower() not in ("accept", "accepted"):
            continue
        if str(d.get("check_id") or "") != "p9_reference" or not str(d.get("reason") or "").strip():
            continue
        if str(d.get("category") or "") not in c2.G4_CATEGORIES:
            continue
        if not d.get("input_hash") or not hashes or str(d["input_hash"]) not in hashes:
            continue  # fix r2 A2-M3: no hash, or the hash of a different reference result: no acceptance
        cats.append(str(d["category"]))
    if not cats:
        return None
    count = "rows" if n is None else f"{n} row{'s' if n != 1 else ''}"
    return f"; reference differs: {count} accepted at G4 ({', '.join(sorted(set(cats)))})"


def source_pairs(coverage: Mapping[str, Any], meas: Sequence[str]) -> tuple[set[str], dict[str, tuple[int, int]]]:
    """Fix r5 M1: (measures complete from source, {measure: (complete units, units carrying it)} for the others that
    are complete in at least one unit), per required (unit, measure) pair."""
    run_level = coverage.get("measures_from_source")
    units = [u for u in coverage.get("units") or [] if u.get("measures") is not None]
    if not units:  # no per-unit record: the run-level list is all there is
        return set(run_level or []), {}
    failed = set(coverage.get("measures_source_failed") or [])
    flags: dict[str, list[bool]] = {}
    for u in units:
        if u.get("measures_from_source") is not None:
            done = set(u["measures_from_source"])
        else:
            done = set(u["measures"]) - set(u.get("measures_not_from_source", u["measures"]) or [])
        for m in u["measures"]:
            flags.setdefault(m, []).append(m in done)
    complete = {m for m in meas if flags.get(m) and all(flags[m]) and m not in failed
                and (run_level is None or m in run_level)}
    partial = {m: (sum(flags[m]), len(flags[m])) for m in meas
               if m not in complete and m not in failed and any(flags.get(m) or [])}
    return complete, partial


def sampling_qualifier(checks: Sequence[Mapping[str, Any]], coverage: Mapping[str, Any] | None) -> tuple[str, str]:
    """(state, qualifier): state complete | incomplete | not run; the qualifier is empty only when complete."""
    p8 = next((c for c in checks if str(c.get("check_id")) == "p8_trace"), None)
    if p8 is None:
        return "not run", "; sampling not run"
    status = str(p8.get("status"))
    if coverage is None:  # fix r2 dec L2: no coverage record means sampling cannot be called complete
        return "incomplete", "; sampling incomplete: coverage not recorded"
    meas = list(coverage.get("deliverable_measures") or
                dict.fromkeys(m for u in coverage.get("units", []) for m in u.get("measures") or []))
    src, partial = source_pairs(coverage, meas)
    not_src = [m for m in meas if m not in src]
    failed = [str(m) for m in coverage.get("measures_source_failed") or []]  # fix r2 A2-M1: surface the failure
    why = f" (source recompute failed: {', '.join(failed)})" if failed else ""
    if partial:  # fix r5 M1: compared somewhere is not complete; say where
        why += " (partially recomputed: " + ", ".join(f"{m} in {k} of {n} units" for m, (k, n) in partial.items()) + ")"
    if meas and not src:
        return "incomplete", STEP_TO_STEP + why
    if not_src:
        return "incomplete", f"; sampling incomplete: {', '.join(not_src)} not recomputed from source{why}"
    if status == "NOT_RUN":
        why = "; ".join(coverage.get("not_run_reasons") or []) or str(p8.get("not_run_reason") or "coverage incomplete")
        return "incomplete", f"; sampling incomplete: {why}"
    return "complete", ""


# ---------------------------------------------------------------- attempt binding (fix r2 A2-H1)
def refuse_stale(run_dir: Path, manifest: Mapping[str, Any], checks: Sequence[Mapping[str, Any]], attempt: int) -> None:
    """Exit 2 (nothing written) when the P7 status or any check row belongs to another attempt or other steps."""
    p = run_dir / "checks" / "reconcile_status.json"
    if p.is_file():
        s = c2.read_input_json(p, "checks/reconcile_status.json")
        if s.get("attempt") is None or not s.get("steps_sha256"):
            raise RefusedError("checks/reconcile_status.json names no attempt or steps hash (written before attempt "
                               "binding): rerun reconcile.py at P7")
        if c2.row_attempt(s) != attempt:
            raise RefusedError(f"checks/reconcile_status.json is from attempt {c2.row_attempt(s)}; the run is at "
                               f"attempt {attempt}: rerun reconcile.py at P7 before the report")
        if str(s["steps_sha256"]) != c2.steps_sha256(manifest):
            raise RefusedError("checks/reconcile_status.json compared other steps than manifest.json now lists "
                               "(steps_sha256 differs): rerun reconcile.py at P7 before the report")
    stale = sorted(f"{c['check_id']} (attempt {c2.row_attempt(c)})" for c in checks if c2.row_attempt(c) != attempt)
    if stale:
        raise RefusedError(f"checks/checks_manifest.csv holds rows from another attempt than {attempt}: "
                           + ", ".join(stale) + "; rerun the phases that write them (P6 to P9) before the report")
    p6 = sorted(f"{c['check_id']} (attempt {c2.row_attempt(c)})" for c in c2.steps_check_rows(run_dir)
                if c2.row_attempt(c) != attempt)  # fix r3 A3-4: the attempt column of run_steps_checks.csv
    if p6:
        raise RefusedError(f"checks/run_steps_checks.csv holds rows from another attempt than {attempt}: "
                           + ", ".join(p6) + "; rerun run_steps.py (P6) before the report")


# ---------------------------------------------------------------- explained numerical failures (fix r2 A2-H4)
def _reasons(decisions: Sequence[Mapping[str, Any]], check: Mapping[str, Any], attempt: int) -> list[str]:
    return list(dict.fromkeys(str(d.get("reason")).strip() for d in decisions
                              if c2._explains(d, check, attempt)))


def numerical_failures(run_dir: Path, classified: Sequence[Mapping[str, Any]], checks: Sequence[Mapping[str, Any]],
                       decisions: Sequence[Mapping[str, Any]], attempt: int) -> list[dict[str, Any]]:
    """P8 trace and P9 logic FAILs explained at G8: their qualifier text and the failing rows to keep on the page.

    A G8 reason does not repair a known source-value or ratio discrepancy (aera H4): it authorises a qualified
    report ("...; sampling: N failures explained at G8 (reasons)", "...; logic: <test> failed, explained at G8
    (reason)"), never "verified against reference" and never "reconciled".
    """
    by_id = {str(c["check_id"]): c for c in checks}
    out = []
    for c in classified:
        if c["final"] != "FAIL-explained" or c.get("status") != "FAIL" or str(c.get("phase")) not in ("P8", "P9"):
            continue
        raw = by_id.get(str(c["check_id"]), c)
        reasons = "; ".join(_reasons(decisions, raw, attempt))
        if str(c.get("phase")) == "P8":
            tr = c2.read_records_if_exists(run_dir / "trace" / "trace_results.csv")
            rows = [str(r.get("sentence")) for r in tr if str(r.get("status")) == "FAIL"]
            n = len(rows) or 1
            text = f"; sampling: {n} failure{'s' if n != 1 else ''} explained at G8 ({reasons})"
        else:
            rows = []
            rel = raw.get("csv_path")
            f = c2.resolve_in_run(run_dir, str(rel)) if rel else None
            if f is not None and f.is_file():
                rows = [f"{r.get('sub_check')}: FAIL (expected {r.get('expected')}, observed {r.get('observed')})"
                        + (f"; {r.get('detail')}" if r.get("detail") else "")
                        for r in c2.read_records(f)[1] if str(r.get("status")) == "FAIL"]
            text = f"; logic: {c['check_id']} failed, explained at G8 ({reasons})"
        out.append({"check_id": c["check_id"], "phase": c.get("phase"), "text": text, "reasons": reasons,
                    "rows": rows or [str(c.get("detail") or "failing rows not recorded")]})
    return out


def cap_consistent(wording: str) -> str:
    """'verified against reference' / 'reconciled...' -> 'consistent...' (an explained numerical failure, A2-H4)."""
    for base in ("verified against reference", "reconciled (provisional)", "reconciled"):
        if wording.startswith(base):
            rest = wording[len(base):]
            return "consistent" + (REF_SUFFIX["independent"] + rest if base.startswith("verified") else rest)
    return wording


def quote(text: Any) -> str:
    """Inline-code span for quoted text (provenance, check details) that block_words must not rewrite (dec L6)."""
    t = str(text).replace("`", "'").replace("|", "/").replace("\n", " ")
    return f"`{t}`" if t.strip() else ""


def block_words(lines: Sequence[str]) -> list[str]:
    """Sampling incomplete: no 'traced' and no 'verified' in the report's own wording (conductor ruling r1).

    Fix r2 dec L6: quoted provenance and check details are inline-code spans (`quote`) and are left verbatim.
    """
    import re

    def fix(part: str) -> str:
        part = re.sub(r"\btraced\b", "looked up", part)
        part = re.sub(r"\bTraced\b", "Looked up", part)
        return re.sub(r"\b[Vv]erified\b", WITHHELD, part)

    out = []
    for ln in lines:
        pieces = re.split(r"(`[^`]*`)", ln)
        out.append("".join(p if p.startswith("`") and p.endswith("`") and len(p) > 1 else fix(p) for p in pieces))
    return out


def p6_checks(run_dir: Path) -> list[dict[str, Any]]:
    """run_steps.py's per-step checks (checks/run_steps_checks.csv) as P6 rows (runctx.steps_check_rows)."""
    return c2.steps_check_rows(run_dir)


def _sample_module() -> Any:
    spec = importlib.util.spec_from_file_location("qt_sample_for_report", Path(__file__).with_name("sample.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("qt_sample_for_report", mod)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- headline table (fix r1 A-LOW)
def _contract(run_dir: Path) -> dict[str, Any] | None:
    p = run_dir / "03_output_contract.json"
    return c2.read_input_json(p, "03_output_contract.json") if p.is_file() else None


def chain_components(card: Mapping[str, Any]) -> list[str]:
    """Every link numerator and denominator of the card's chain, plus the final pair, in chain order."""
    ch = card.get("chain")
    if not isinstance(ch, Mapping):
        return []
    out: list[str] = []
    for link in ch.get("links") or []:
        out += [str(link.get("numerator")), str(link.get("denominator"))]
    fin = ch.get("final") or {}
    out += [str(fin[k]) for k in ("numerator", "denominator") if fin.get(k)]
    return list(dict.fromkeys(x for x in out if x and x != "None"))


def grain_columns(manifest: Mapping[str, Any], contract: Mapping[str, Any] | None,
                  columns: Sequence[cc.Column]) -> list[str]:
    """Grain of the deliverable: contract keys (or grain), else the final step's grain, else its non-numeric columns."""
    names = [c.name for c in columns]
    for cand in ((contract or {}).get("keys"), (contract or {}).get("grain"),
                 (manifest["steps"][-1].get("grain") if manifest.get("steps") else None)):
        if cand and all(k in names for k in cand):
            return list(cand)
    return [c.name for c in columns if canon.type_class(c.type) not in ("int", "float", "decimal")]


def fmt_cell(v: Any, type_text: str, scale: int | None) -> str:
    """A headline cell: FLOAT rounded to the contract scale (default 4), DECIMAL to its contract scale."""
    if v is None:
        return "NULL"
    tc = canon.type_class(type_text)
    if isinstance(v, bool):
        return str(v)
    if tc == "float" or isinstance(v, float):
        return f"{float(v):.{DEFAULT_FLOAT_SCALE if scale is None else int(scale)}f}"
    if tc == "decimal" and scale is not None:
        return str(decimal.Decimal(str(v)).quantize(decimal.Decimal(1).scaleb(-int(scale)), decimal.ROUND_HALF_EVEN))
    return str(v)


def headline_lines(run_dir: Path, manifest: Mapping[str, Any], card: Mapping[str, Any], hl: str) -> list[str]:
    d = next((d for d in manifest["deliverables"] if d.get("side") == "B"), None)
    if d is None:
        raise c2.InputMissingError("manifest.json has no deliverable B")
    path = c2.resolve_in_run(run_dir, d["csv"]["path"])
    cols, rows = c2.read_records(path, "deliverable CSV")
    if hl not in cols:
        return [f"Headline field `{hl}` is not a column of the deliverable output."]
    types = {c.name: c.type for c in cc.read_sidecar(path).columns}
    contract = _contract(run_dir)
    scales = {str(c.get("name")): c.get("scale") for c in (contract or {}).get("columns") or [] if isinstance(c, Mapping)}
    keys = [k for k in grain_columns(manifest, contract, cc.read_sidecar(path).columns) if k != hl]
    comps = [c for c in chain_components(card) if c in cols and c not in keys and c != hl]
    if not comps:
        comps = [c for c in cols if c not in keys and c != hl][:3]
    show = keys + comps + [hl]
    ordered = sorted(rows, key=lambda r: tuple((r.get(k) is None, r.get(k) if r.get(k) is not None else 0) for k in keys))
    out = [f"Headline field `{hl}` ({len(rows)} row{'s' if len(rows) != 1 else ''} in the deliverable, ordered by "
           f"{', '.join(keys) or 'file order'}; FLOAT columns rounded to the contract scale, else "
           f"{DEFAULT_FLOAT_SCALE} decimals):", "", "| " + " | ".join(show) + " |", "|" + "---|" * len(show)]
    for r in ordered[:24]:
        out.append("| " + " | ".join(fmt_cell(r.get(k), types.get(k, ""), scales.get(k)) for k in show) + " |")
    if len(rows) > 24:
        out.append(f"| ... {len(rows) - 24} more rows in the deliverable CSV |")
    return out


# ---------------------------------------------------------------- page pieces
def _bytes(n: Any) -> str:
    return f"{int(n):,}" if isinstance(n, (int, float)) and not isinstance(n, bool) else "not recorded"


def budget_line(manifest: Mapping[str, Any]) -> str:
    """'spent / budget' from manifest.budget (package B writes it; read defensively)."""
    b = manifest.get("budget") or {}
    if not isinstance(b, Mapping) or b.get("spent_bytes") is None or b.get("budget_bytes") is None:
        return "- Budget: not recorded in manifest.json"
    rem = f" (remaining {_bytes(b['remaining_bytes'])})" if b.get("remaining_bytes") is not None else ""
    # fix r4 LOW: the authorised amounts of unreconciled jobs are shown too (they count against the budget)
    held = f"; held {_bytes(b['held_bytes'])} bytes for unreconciled jobs" if b.get("held_bytes") else ""
    return f"- Budget: spent {_bytes(b['spent_bytes'])} / budget {_bytes(b['budget_bytes'])} bytes{rem}{held}"


def comparison_lines(comp: Mapping[str, Any]) -> list[str]:
    out = [f"- Comparison: {comp['status']}"]
    for c in comp["comparisons"]:
        bits = [f"{c.get('comparison')}: {c.get('status')}"]
        if c.get("classes"):
            bits.append("classes " + ", ".join(map(str, c["classes"])))
        if c.get("inconclusive_reason"):
            bits.append("inconclusive: " + str(c["inconclusive_reason"]))
        if c.get("method"):
            bits.append("method: " + str(c["method"]))
        nr = c.get("not_run_at_level") or []
        bits.append("not run at this level: " + ("; ".join(map(str, nr)) if nr else "none"))
        if c.get("reason"):
            bits.append(str(c["reason"]))
        out.append("  - " + "; ".join(bits))
    if comp.get("adequacy_not_run"):
        out.append("  - Adequacy counters not run: " + ", ".join(map(str, comp["adequacy_not_run"])))
    return out


def coverage_line(u: Mapping[str, Any]) -> str:
    """One per unit: what was compared against what, what was not recomputed (from source), columns not compared."""
    if u.get("status") == "NO_MEASURE":  # fix r6: trace.py's unit with no deliverable measure, lookups all N/A
        return f"- Coverage, {u['unit']}: no measure to trace (no deliverable measure; every lookup not applicable)"
    comp = u.get("compared") or {}
    parts = [("compared " + "; ".join(f"{m} against {', '.join(ups)}" for m, ups in comp.items())) if comp
             else "compared no deliverable measure"]
    if u.get("measures_not_recomputed"):
        parts.append("not recomputed: " + ", ".join(u["measures_not_recomputed"]))
    parts.append("not recomputed from source: " + (", ".join(u.get("measures_not_from_source") or []) or "none"))
    parts.append("columns not compared: " + (", ".join(u.get("columns_not_compared") or []) or "none"))
    return f"- Coverage, {u.get('unit')}: " + "; ".join(parts)


def _steps_sorted(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return sorted(manifest["steps"], key=lambda s: (s.get("step") is None, s.get("step") or 0, str(s.get("name"))))


# ---------------------------------------------------------------- build
def build(run_dir: Path) -> dict[str, Any]:
    """Assemble the verdict and write report.md, report_status.json and manifest.sha256."""
    run_dir = Path(run_dir)
    manifest = c2.load_manifest(run_dir)
    rc = c2.load_run_config_raw(run_dir)
    level = c2.run_level(run_dir)
    card = c2.card_fields(run_dir / "02_metric_card.md")
    decisions = c2.load_decisions(run_dir)
    if not (run_dir / "checks" / c2.CHECKS_MANIFEST).is_file():
        raise c2.InputMissingError("checks/checks_manifest.csv missing (P8 and P9 write it)")
    checks = c2.load_checks(run_dir)
    attempt = c2.current_attempt(run_dir)
    refuse_stale(run_dir, manifest, checks, attempt)  # fix r2 A2-H1: before anything is written
    decisions_now = [d for d in decisions if c2.row_attempt(d) == attempt]
    known = {str(c["check_id"]) for c in checks}
    checks += [c for c in p6_checks(run_dir) if c["check_id"] not in known]
    ref = c2.read_input_json(run_dir / "checks" / "logic_reference.json", "checks/logic_reference.json")
    reference = str(ref.get("outcome"))
    prov = ref.get("provenance") or {}
    classified = c2.classify_checks([c for c in checks if c.get("family") not in ("reference", "comparison")],
                                    rc.get("required_checks"), decisions, attempt=attempt)
    comp = comparison_status(run_dir, checks)
    coverage = c2.read_input_json(run_dir / TRACE_COVERAGE) if (run_dir / TRACE_COVERAGE).is_file() else None
    sampling, qualifier = sampling_qualifier(checks, coverage)
    # sampling is its own dimension: a p8 row that did not run qualifies the verdict, it never flips it
    unexplained_checks = [c for c in classified if c["required"] and c["final"] == "FAIL-unexplained"
                          and not (str(c.get("phase")) == "P8" and c.get("status") == "NOT_RUN")]
    unexplained = bool(unexplained_checks) or comp["status"] != "PASS"
    numeric = numerical_failures(run_dir, classified, checks, decisions, attempt)
    named = {n["check_id"] for n in numeric}
    explained_n = sum(1 for c in classified if c["final"] == "FAIL-explained" and c["check_id"] not in named)
    ref_accepted = reference_acceptance(run_dir, ref, decisions)
    indep = manifest.get("independence", {})
    ind = str(indep.get("level", "IND-0"))
    pin = manifest.get("pin", {})
    pin_status = str(pin.get("pin_status", "PROVISIONAL"))
    row, wording, extra = verdict(ind, pin_status, reference, prov.get("independence"), unexplained,
                                  ref_type=prov.get("type"), comparison=comp["wording"], ref_accepted=ref_accepted)
    if no_reference_declared(rc):  # fix r11 L6: the sentence follows from reference: none, it is not a finding
        extra = [no_reference_sentence(rc) if e == ACCURACY else e for e in extra]
    if row > 2 and qualifier:
        if wording == "verified against reference":
            wording = "reconciled" + REF_SUFFIX["independent"]
        extra = [e for e in extra if e != NOT_INDEPENDENT] + (
            ["This reference is not independent (IND-5, spec 4.3)."] if NOT_INDEPENDENT in extra else [])
        wording += qualifier
    if numeric and row > 2:  # fix r2 A2-H4: qualified, never "verified against reference", never "reconciled"
        wording = cap_consistent(wording)
        extra = [e for e in extra if e != NOT_INDEPENDENT] + (
            ["This reference is not independent (IND-5, spec 4.3)."] if NOT_INDEPENDENT in extra else [])
        wording += "".join(n["text"] for n in numeric)
    if explained_n and row > 2:
        wording += f", with {explained_n} explained exception{'s' if explained_n != 1 else ''}"
    labels = []
    if indep.get("isolation_attested_not_enforced"):
        labels.append("(isolation attested, not enforced)")
    if ind == "IND-4" and any(str(d.get("gate")).upper() == "G4" for d in decisions_now):
        labels.append("author-adjudicated (IND-4)")
    if indep.get("dependent_revision"):
        labels.append("dependent revision (IND-0)")
    if reference != "agree":  # with "agree" the verdict suffix already names the reference's independence (A-M1)
        if prov.get("type") == "synthetic" or prov.get("independence") == "synthetic":
            labels.append("(synthetic reference: a demonstration, not evidence)")
        elif prov.get("independence") == "same_source" or prov.get("type") == "bi_dashboard":
            labels.append("(same-source reference, not independent)")
    g16 = []
    if reference == "none":
        g16.append("no reference")
    if ref_accepted:
        g16.append("reference differs, accepted at G4")
    if pin_status == "PROVISIONAL":
        g16.append("PROVISIONAL pin")
    claim = (card.get("headline") or {}).get("claim")
    if headline_unsupported(claim, wording):
        g16.append(f"unsupported headline flag: the card claims {claim!r}, the verdict is {wording!r}"
                   + (" (pin PROVISIONAL)" if is_provisional(wording) else ""))
    manifest = mf.register_late_outputs(run_dir, rc, manifest)
    ctx = {"manifest": manifest, "rc": rc, "level": level, "card": card, "decisions": decisions_now,
           "numeric": numeric,
           "classified": classified, "comp": comp, "reference": reference, "prov": prov, "ind": ind, "pin": pin,
           "wording": wording, "labels": labels, "extra": extra, "g16": g16, "coverage": coverage,
           "ref_accepted": ref_accepted, "sampling": sampling}
    lines = render(run_dir, ctx)
    if sampling != "complete":
        lines = block_words(lines)
    c2.write_text(run_dir / "report.md", "\n".join(lines) + "\n")
    status = {"reported_utc": c2.utc_now_text(), "verdict": wording, "verdict_row": row, "labels": labels,
              "independence": ind, "pin_status": pin_status, "reference": reference, "confidence_level": level,
              "comparison": comp["status"], "sampling": sampling,
              "unexplained": [c["check_id"] for c in unexplained_checks] + (["P7 comparison"]
                                                                            if comp["status"] != "PASS" else []),
              "explained_exceptions": explained_n, "g16_needed": bool(g16), "g16_reasons": g16,
              "attempt": attempt, "explained_numerical_failures": [n["check_id"] for n in numeric]}
    c2.write_json(run_dir / "report_status.json", status)
    mf.register_late_outputs(run_dir, rc)
    mf.seal(run_dir)
    return status


def render(run_dir: Path, ctx: Mapping[str, Any]) -> list[str]:
    """report.md lines."""
    manifest, rc, level, card = ctx["manifest"], ctx["rc"], ctx["level"], ctx["card"]
    classified, reference, prov, ind, pin = ctx["classified"], ctx["reference"], ctx["prov"], ctx["ind"], ctx["pin"]
    lad = load_ladder()
    out = [f"# Report: run {manifest['run_id']}", "", f"**Verdict: {ctx['wording']}** " + " ".join(ctx["labels"]), ""]
    out += [f"- {e}" for e in ctx["extra"]]
    if rc.get("owner_mode") == "research":
        out.append("- owner mode, non-compliant with D-08, research only")
    reasons = list(pin.get("provisional_reasons") or []) + [f"unpinnable: {u}" for u in pin.get("unpinnable") or []]
    out += [f"- Independence: {ind}", f"- Pin: {pin.get('pin_status', 'PROVISIONAL')} (route {pin.get('route', '?')}, "
            f"as_of_utc {pin.get('as_of_utc')})" + (f"; reasons: {'; '.join(reasons)}" if reasons else ""),
            f"- Confidence level: {level} {lad.levels[level].name}", budget_line(manifest)]
    out += comparison_lines(ctx["comp"])
    out += [f"- Reference: {reference}" + (f" ({prov.get('type')}, source {quote(prov.get('source'))}, extraction "
                                            f"{quote(prov.get('extraction_method'))}, as_of {prov.get('as_of')}, grain "
                                            f"{quote(prov.get('grain'))}, basis {quote(prov.get('basis'))}, tolerance "
                                            f"{prov.get('tolerance')})" if reference != "none" else "")
            + (f"; {ctx['ref_accepted'].lstrip('; ')}" if ctx["ref_accepted"] else ""), ""]
    hl = (card.get("headline") or {}).get("field")
    out += ["## Headline", ""]
    if hl:
        out += headline_lines(run_dir, manifest, card, str(hl))
    out += ["", "## Required checks (spec 6.4)", "", "| check | phase | required | result | detail |", "|---|---|---|---|---|"]
    for c in sorted(classified, key=lambda c: (str(c.get("phase")), c["check_id"])):
        out.append(f"| {c['check_id']} | {c.get('phase') or '-'} | {'yes' if c['required'] else 'no'} | {c['final']} | "
                   f"{quote(c['detail'] or c.get('not_run_reason') or '')} |")
    not_run = sorted(set(lad.levels[100].checks) - set(lad.levels[level].checks))
    out += ["", "## Checks not run at this level", ""]
    out += [f"- {x}" for x in not_run] or ["- none: every ladder check can run at this level"]
    out += [f"- {c['check_id']}: {quote(c.get('not_run_reason') or 'not run')}" for c in classified
            if c["final"] == "NOT-RUN-at-level"]
    out += [f"- {c['check_id']}: {quote(c['detail'])}" for c in classified
            if c.get("status") == "NOT_RUN" and c["final"].startswith("FAIL")]
    if ctx.get("numeric"):  # fix r2 A2-H4: the failing rows stay visible
        out += ["", "## Explained numerical failures (G8)", ""]
        for n in ctx["numeric"]:
            out.append(f"- {n['check_id']} ({n['phase']}): explained at G8: {quote(n['reasons'])}; failing rows:")
            out += [f"  - {r}" for r in n["rows"]]
    out += ["", "## Steps", "", "| step | rows in -> out (reason) | level (requested -> effective) | artifacts | replay | "
            "status |", "|---|---|---|---|---|---|"]
    for s in _steps_sorted(manifest):
        rin = s.get("rows_in")
        why = s.get("row_delta_reason")
        out.append(f"| {s['name']} | {'not recorded' if rin is None else rin} -> {s.get('rows_out')} "
                   f"({why or 'not recorded'}) | {s.get('requested_level')} -> {s.get('effective_level')} | "
                   f"{', '.join(s.get('artifacts', []))} | {s.get('replay_status', 'NONE')} | {s.get('status')} |")
    degraded = [s for s in manifest["steps"] if s.get("degraded") or s.get("status") == "degraded"]
    out += [""] + [f"- Degraded: {s['name']}: {s.get('degraded_reason') or 'over a local cap'}" for s in degraded]
    out += [f"- Not replayed locally: {s['name']}: {s.get('replayable_reason')}" for s in manifest["steps"]
            if str(s.get("replayable_reason") or "").startswith("BIGNUMERIC")]
    for gate, what in (("G10", "Hand split (labelled; B vs chain(B) mandatory)"), ("G9", "Lint waiver")):
        out += [f"- {what}: {d.get('reason')}" for d in ctx["decisions"] if str(d.get("gate")).upper() == gate]
    out += sample_lines(run_dir, ctx.get("coverage"))
    out += ["", "## Limitations", ""]
    lim = ["Whole-vs-own-parts cannot catch an error shared by B and its split (X, T13)."] if ind == "IND-0" else []
    if reference == "none":
        lim.append(no_reference_sentence(rc) if no_reference_declared(rc) else ACCURACY)
    lim.append("Sampling assurance concerns the defined sampling unit and the residual population; it does not "
               "establish semantic correctness.")
    cov = ctx.get("coverage")
    if cov is not None and not cov.get("measures_from_source"):
        lim.append("The trace compared each step with the step before it; no deliverable measure was recomputed "
                   "from a source table (spec 6.2).")
    elif ctx.get("sampling") == "incomplete":
        lim.append("Sampling is incomplete: the measures named in the verdict were not recomputed from source "
                   "(spec 6.2); the sample does not cover them.")
    elif ctx.get("sampling") == "not run":
        lim.append("Sampling did not run (no p8_trace result).")
    out += [f"- {x}" for x in lim]
    files = manifest.get("files", [])
    out += ["", "## Retention", ""]
    if files:
        dates = sorted(str(f["retention_until"]) for f in files)
        out.append(f"- {len(files)} manifest file(s); retention_until from {dates[0]} to {dates[-1]} "
                   f"(created + {(rc.get('retention') or {}).get('months', 6)} months unless overridden).")
    else:
        out.append("- no files listed in the manifest")
    out.append("- Deletion only by `close.py --apply`, run by Max; unlisted files are never touched.")
    out += ["", "## Close", ""]
    out.append(f"- G16 acknowledgement needed: {'; '.join(ctx['g16'])}" if ctx["g16"] else "- G16 not needed.")
    out += files_lines(run_dir)
    return out


def no_reference_declared(rc: Mapping[str, Any]) -> bool:
    """Fix r11 L6: run_config.json reference.type is none (or no reference block): Max supplied no reference."""
    ref = rc.get("reference")
    return not isinstance(ref, Mapping) or str(ref.get("type") or "none").strip().lower() == "none"


def no_reference_sentence(rc: Mapping[str, Any]) -> str:
    """Fix r12 L4: NO_REFERENCE names G1a only when run_config.json has a reference block with type none; with no
    block at all there is no G1a record to point to, so NO_REFERENCE_DECLARED."""
    return NO_REFERENCE if isinstance(rc.get("reference"), Mapping) else NO_REFERENCE_DECLARED


def files_lines(run_dir: Path) -> list[str]:
    """Fix r11 L5: where the deliverable, the SQL and the gate log are; existing files only, relative paths."""
    run_dir = Path(run_dir)
    out = ["", "## Files", "", f"- Run folder: `{run_dir.resolve().as_posix()}`"]
    if (run_dir / "view.html").is_file():  # round 13: the run viewer, derived and unlisted
        out.append("- `view.html`: the run viewer, open it in a browser (regenerate with view.py)")
    fixed = (("outputs/B_whole.csv", "the deliverable"), ("B.sql", "the query as written"),
             ("executed/B.run.sql", "the query as run (pin applied)"))
    out += [f"- `{rel}`: {what}" for rel, what in fixed if (run_dir / rel).is_file()]
    ex = run_dir / "executed"
    steps = sorted((p.name for p in ex.glob("*.run.sql") if STEP_RUN.fullmatch(p.name)),
                   key=lambda n: int(n.split(".", 1)[0])) if ex.is_dir() else []  # fix r12 L3: numbered steps only
    if steps:
        out.append(f"- `executed/{steps[0]}`" + (f" to `executed/{steps[-1]}`" if len(steps) > 1 else "")
                   + ": each step as run (pin applied)")
    if (ex / "A.run.sql").is_file():
        out.append("- `executed/A.run.sql`: the second author's query as run (pin applied)")
    dirs = (("steps", "each step as written"), ("trace/sql", "the sampling queries"))
    out += [f"- `{rel}/`: {what}" for rel, what in dirs if (run_dir / rel).is_dir()]
    more = (("decisions.csv", "the gate log: every decision, with Max's answer"),
            ("trace_report.md", "the sampling report"), ("13_table_decision.md", "the table decision"))
    out += [f"- `{rel}`: {what}" for rel, what in more if (run_dir / rel).is_file()]
    if (run_dir / "manifest.json").is_file():  # manifest.sha256 is written by mf.seal right after this page
        out.append("- `manifest.json` + `manifest.sha256`: every file of the run with its hash, and the seal")
    return out


def sample_lines(run_dir: Path, coverage: Mapping[str, Any] | None) -> list[str]:
    """Sample sentences, per-unit assurance (recomputed, so no stale wording) and per-unit trace coverage."""
    out = ["", "## Sample sentences (P8)", ""]
    tr = c2.read_records_if_exists(run_dir / "trace" / "trace_results.csv")
    order = {"FAIL": 0, "NOT_TRACED": 1, "PASS": 2}
    out += [f"- {r['sentence']}" for r in sorted(tr, key=lambda r: (order.get(str(r["status"]), 3), str(r["lookup_id"])))]
    if not tr:
        out.append("- no trace results")
    sm = run_dir / "samples" / "sample_manifest.json"
    if sm.is_file():
        man = c2.read_input_json(sm)
        smod = _sample_module()
        for u in man.get("units", []):
            fails = len({r["row_id"] for r in tr if r["unit"] == u["unit"] and r["status"] == "FAIL"
                         and r["selection"] in ("random", "census")})
            if "residual_rows" in u and "census" in u:
                text = smod.assurance(int(man["n"]), int(u["residual_rows"]), bool(u["census"]), fails)
            else:
                text = str(u.get("wording_if_no_deviation", "")).replace("; no sampling risk for this unit", "")
            out.append(f"- Assurance, {u['unit']}: {text}")
    if coverage is not None:
        out += [coverage_line(u) for u in coverage.get("units", [])]
        if not coverage.get("measures_from_source"):
            out.append("- Sampling: step-to-step only: no deliverable measure was recomputed from a source table.")
    else:
        out.append("- Coverage: not recorded (trace/trace_coverage.json missing): what was compared is not shown.")
    return out


def _main(a: Any) -> int:
    c2.phase_check(a.run_dir, ["P11"])
    st = build(a.run_dir)
    print(f"verdict: {st['verdict']} (row {st['verdict_row']}); G16 {'needed' if st['g16_needed'] else 'not needed'}")
    return int(ExitCode.PASS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    return c2.run_cli_args(_main, c2.base_parser("report.py", "P11 report and wording (spec 4.4)"), argv)


if __name__ == "__main__":
    sys.exit(main())

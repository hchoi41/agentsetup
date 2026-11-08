"""Role prompts. Inputs follow the charter's 'receives' lists; the cold reviewer gets artifact + spec only."""
from __future__ import annotations

import json

JSON_ONLY = "Return ONLY one JSON object in a ```json fence, no other text."


def _block(title: str, body: str) -> str:
    return f"\n===== {title} =====\n{body}\n"


def builder(job, plan: str, spec: str, sources: str, prev_artifact: str | None, improvements: list | None, extra: str = "") -> str:
    task = ("Build the artifact described by the plan and spec." if prev_artifact is None else
            "Revise the artifact. The improvements list below is your sole work list; resolve every entry and change nothing else.")
    return (f"You are the BUILDER in a tikitaka loop. {task}\n"
            "Reply with exactly these two delimited sections and nothing else (the artifact may contain code fences):\n"
            "<<<ARTIFACT\n<the complete artifact text>\nARTIFACT>>>\n"
            "<<<CHANGES\n<one line per change: [item ids] what changed and why; for a first build: 'initial build'>\nCHANGES>>>\n"
            "The artifact must not contain change history, reviewer references or reasoning."
            + _block("PLAN", plan) + _block("SPEC", spec) + (_block("SOURCES", sources) if sources else "")
            + (_block("CURRENT ARTIFACT", prev_artifact) if prev_artifact is not None else "")
            + (_block("IMPROVEMENTS (sole work list)", "\n".join(improvements)) if improvements else "")
            + (_block("ADDITIONAL INPUT", extra) if extra else ""))


def _review_schema(role: str, seat: str, model: str, version: str, letter: str) -> str:
    return (f'{JSON_ONLY}\n{{ "role": "{role}", "seat": "{seat}", "model": "{model}", "artifact_version": "{version}",\n'
            f'  "verdict": "approve|revise",\n'
            f'  "issues": [ {{ "id": "{letter}-{seat}-1", "location": "section / quote", "problem": "...", "evidence": "quote",\n'
            f'                "fix": "concrete change", "severity": "blocking|major|minor", "fixable_in_loop": true }} ] }}\n'
            f"Ids: prefix {letter}-{seat}- numbered 1, 2, 3. fixable_in_loop = true only if fixable by editing the artifact.\n"
            "Report only real problems; an empty issues list with verdict approve is valid.")


def accuracy(role, version: str, artifact: str, spec: str, sources: str) -> str:
    return ("You are the ACCURACY REVIEWER in a tikitaka loop. Check the ARTIFACT for correctness against the SPEC and SOURCES: "
            "contradictions, misstatements, unmet spec items, internal contradictions, unenforceable rules. Do not judge style.\n"
            + _review_schema(role.name, role.seat, f"{role.model}/{role.effort}", version, "A")
            + (_block("SOURCES", sources) if sources else "") + _block("SPEC", spec) + _block(f"ARTIFACT {version}", artifact))


def cold(role, version: str, artifact: str, spec: str) -> str:
    return ("You are the COLD REVIEWER in a tikitaka loop. You get only the artifact and its spec, by design. "
            "Flag what is unclear, ambiguous, or would force a fresh implementer to guess; unmet spec items; internal contradictions.\n"
            + _review_schema(role.name, role.seat, f"{role.model}/{role.effort}", version, "C")
            + _block("SPEC", spec) + _block(f"ARTIFACT {version}", artifact))


def judge(version: str, rnd: int, max_rounds: int, plan: str, process: str, artifact: str, spec: str, sources: str,
          reviews: list, prior: list) -> str:
    return (f"You are the JUDGE in a tikitaka loop (round {rnd} of at most {max_rounds}). You evaluate, assimilate and consolidate.\n"
            "Judge the PLAN, the PROCESS (how it was built), the OUTPUT and the REVIEWS. Decide EVERY review item: improve / keep "
            "(artifact stays; issue noted) / reject (issue wrong, duplicate or out of scope), with a reason. Merge duplicates. "
            "You may add your own items in judge_items (ids J-1, J-2...). Each improvements entry starts with the item id(s) in brackets "
            "and must cover every improve item and judge item. improvement_necessary is 'yes' iff at least one item drives iteration; "
            "say 'no' when what remains is minor or taste.\n"
            f'{JSON_ONLY}\n{{ "artifact_version": "{version}", "plan_assessment": "...", "process_assessment": "...", "output_assessment": "...",\n'
            '  "items": [ { "id": "A-seat-1", "decision": "improve|keep|reject", "reason": "...", "fixable_override": null } ],\n'
            '  "judge_items": [ { "id": "J-1", "location": "...", "problem": "...", "fix": "...", "severity": "minor", "fixable_in_loop": true } ],\n'
            '  "improvements": [ "[A-seat-1, C-seat-2] concrete edit" ],\n  "improvement_necessary": "yes|no" }'
            + _block("PLAN", plan) + _block("PROCESS RECORD", process)
            + (_block("SOURCES", sources) if sources else "") + _block("SPEC", spec)
            + _block(f"ARTIFACT {version}", artifact)
            + _block("REVIEWS (gate-validated)", json.dumps(reviews, ensure_ascii=False, indent=1))
            + (_block("YOUR PRIOR VERDICTS", json.dumps(prior, ensure_ascii=False, indent=1)) if prior else ""))


def agreement(decision_type: str, action: str, reasons: str, evidence: str, model: str) -> str:
    return (f"You are the JUDGE. The orchestrator proposes a major decision of type '{decision_type}'. Agree only if it is right "
            "on the evidence; otherwise disagree and say what must change.\n"
            f'{JSON_ONLY}\n{{ "type": "{decision_type}", "proposal": {{ "by": "orchestrator", "action": {json.dumps(action)}, '
            f'"reasons": {json.dumps(reasons)} }},\n  "role": "judge", "model": "{model}", "agree": true, "reasons": "..." }}'
            + _block("EVIDENCE", evidence))


def qa(functions: list, version: str, artifact: str, seat: str) -> str:
    lst = "\n".join(f"Q{i + 1}. {f}" for i, f in enumerate(functions))
    return ("You are QA/QC: a final check, not a review for improvement. For each planned function decide pass/fail for the FINAL ARTIFACT. "
            "For a pass, 'evidence' must be an exact verbatim quote (20-300 chars, no ellipses) from the artifact; code verifies it. "
            "For a fail, quote the closest passage or '' and say what is missing.\n"
            f'{JSON_ONLY}\n{{ "role": "qa_qc", "seat": "{seat}", "artifact_version": "{version}", '
            f'"results": [ {{ "id": "Q1", "result": "pass|fail", "evidence": "verbatim quote", "note": "short" }} ] }}\n'
            f"Return exactly {len(functions)} results in order." + _block("PLANNED FUNCTIONS", lst)
            + _block(f"FINAL ARTIFACT {version}", artifact))


def qa_fix_acceptance(qa_result: dict, fix_reviews: list, artifact: str, model: str) -> str:
    return ("You are the JUDGE. A bounded fix (only the functions QA/QC marked fail) was reviewed. Accept it only if every fix review "
            "approves or raises only minor issues.\n"
            f'{JSON_ONLY}\n{{ "accept": true, "reasons": "...", "model": "{model}" }}'
            + _block("QA RESULT", json.dumps(qa_result, ensure_ascii=False, indent=1))
            + _block("FIX REVIEWS", json.dumps(fix_reviews, ensure_ascii=False, indent=1)) + _block("FIXED ARTIFACT", artifact))


def auditor(digest: str) -> str:
    return ("You are the AUDITOR. Write AUDIT.md for a human reader: what was planned, what each role did, what the reviewers found, "
            "what the judge decided and why (including rejected items), consultations, overrides, cut-offs, seat replacements, "
            "incomplete-review stops, the final outcome and QA/QC result. Link each claim to its evidence file path from the digest. "
            "Plain language; explain terms; no unexplained internal IDs. Reply with the Markdown only."
            + _block("RUN DIGEST (from the run directory)", digest))


def extension(role, inputs: str) -> str:
    return (f"You are the {role.name.upper()} in a tikitaka loop. {role.extra.get('instruction', '')}\n"
            + _block("INPUTS", inputs))

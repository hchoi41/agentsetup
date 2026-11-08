"""Deterministic gates (charter §6.1, §7, QA). Every model output crosses one of these before use."""
from __future__ import annotations

import json
import re

SEVERITIES = ("blocking", "major", "minor")
REVIEW_ROLES = ("accuracy_reviewer", "cold_reviewer", "accuracy_reviewer_2", "cold_reviewer_2")
DECISION_TYPES = ("plan_approval", "milestone", "finish", "qa_failure_handling")
ROLE_LETTER = {"accuracy_reviewer": "A", "accuracy_reviewer_2": "A", "cold_reviewer": "C", "cold_reviewer_2": "C"}


class GateError(ValueError):
    pass


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply (fenced or bare). Empty/truncated -> GateError."""
    if not text or not text.strip():
        raise GateError("empty output")
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise GateError("no JSON object")
    try:
        d = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise GateError(f"invalid JSON: {e}") from None
    if not isinstance(d, dict):
        raise GateError("JSON is not an object")
    return d


def _need(obj: dict, keys, where: str):
    miss = [k for k in keys if k not in obj]
    if miss:
        raise GateError(f"{where}: missing {miss}")


def validate_review(d: dict, *, role: str, seat: str) -> dict:
    _need(d, ["role", "seat", "model", "artifact_version", "verdict", "issues"], "review")
    if d["role"] != role:
        raise GateError(f"review role {d['role']!r} != dispatched role {role!r}")
    if d["verdict"] not in ("approve", "revise"):
        raise GateError(f"bad verdict {d['verdict']!r}")
    if not isinstance(d["issues"], list):
        raise GateError("issues must be a list")
    prefix = f"{ROLE_LETTER[role]}-{seat}-"
    for i in d["issues"]:
        _need(i, ["id", "location", "problem", "evidence", "fix", "severity", "fixable_in_loop"], f"issue {i.get('id')}")
        if not str(i["id"]).startswith(prefix):
            raise GateError(f"issue id {i['id']!r} must start with {prefix!r}")
        if i["severity"] not in SEVERITIES:
            raise GateError(f"issue {i['id']}: bad severity {i['severity']!r}")
        if not isinstance(i["fixable_in_loop"], bool):
            raise GateError(f"issue {i['id']}: fixable_in_loop must be boolean")
    return d


def check_unique_ids(reviews: list, verdict: dict | None = None):
    ids = [i["id"] for r in reviews for i in r["issues"]]
    if verdict:
        ids += [i["id"] for i in verdict.get("judge_items", [])]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        raise GateError(f"duplicate issue ids: {dup}")


def validate_verdict(d: dict, reviews: list) -> dict:
    """Judge verdict: schema, decisions, fixable rule, improvements coverage, consistency (charter §7)."""
    _need(d, ["artifact_version", "plan_assessment", "process_assessment", "output_assessment",
              "items", "improvements", "improvement_necessary"], "verdict")
    if d["improvement_necessary"] not in ("yes", "no"):
        raise GateError("improvement_necessary must be 'yes' or 'no'")
    review_items = {i["id"]: i for r in reviews for i in r["issues"]}
    judge_items = d.get("judge_items", []) or []
    for j in judge_items:
        _need(j, ["id", "location", "problem", "fix", "severity", "fixable_in_loop"], f"judge item {j.get('id')}")
        if not re.fullmatch(r"J-\d+", str(j["id"])):
            raise GateError(f"judge item id {j['id']!r} must be J-<n>")
    check_unique_ids(reviews, d)
    decided = set()
    driving = set()
    for it in d["items"]:
        _need(it, ["id", "decision", "reason"], "item")
        if it["decision"] not in ("improve", "keep", "reject"):
            raise GateError(f"item {it['id']}: bad decision {it['decision']!r}")
        if it["id"] not in review_items:
            raise GateError(f"item {it['id']} is not an issue of this round")
        decided.add(it["id"])
        fixable = review_items[it["id"]]["fixable_in_loop"]
        ov = it.get("fixable_override")
        if ov is not None:
            if not isinstance(ov, bool) or not it.get("reason"):
                raise GateError(f"item {it['id']}: fixable_override needs a boolean and a reason")
            fixable = ov
        if it["decision"] == "improve":
            if not fixable:
                raise GateError(f"item {it['id']}: improve on a non-fixable item")
            driving.add(it["id"])
    undecided = sorted(set(review_items) - decided)
    if undecided:
        raise GateError(f"review items without a decision: {undecided}")
    driving |= {j["id"] for j in judge_items if j["fixable_in_loop"]}
    covered = set()
    for e in d["improvements"]:
        m = re.match(r"\s*\[([^\]]+)\]", str(e))
        if not m:
            raise GateError(f"improvements entry without item id(s): {str(e)[:60]!r}")
        ids = {x.strip() for x in m.group(1).split(",")}
        bad = ids - driving
        if bad:
            raise GateError(f"improvements entry references non-driving id(s): {sorted(bad)}")
        covered |= ids
    if driving - covered:
        raise GateError(f"driving items missing from improvements: {sorted(driving - covered)}")
    if (d["improvement_necessary"] == "yes") != bool(driving):
        raise GateError(f"inconsistent verdict: improvement_necessary={d['improvement_necessary']} with {len(driving)} driving item(s)")
    d["_driving"] = sorted(driving)
    return d


def validate_agreement(d: dict, *, decision_type: str) -> dict:
    _need(d, ["type", "proposal", "role", "model", "agree", "reasons"], "agreement")
    if d["type"] != decision_type or decision_type not in DECISION_TYPES:
        raise GateError(f"agreement type {d['type']!r} != {decision_type!r}")
    p = d.get("proposal") or {}
    if p.get("by") != "orchestrator" or not p.get("action"):
        raise GateError("agreement without an orchestrator proposal is invalid")
    if not isinstance(d["agree"], bool):
        raise GateError("agree must be boolean")
    if d["role"] != "judge":
        raise GateError("only the judge gives agreement")
    return d


def validate_qa(d: dict, *, functions: list, artifact_text: str) -> dict:
    """QA/QC: one pass/fail per planned function; pass evidence must be a verbatim quote (whitespace-normalised)."""
    _need(d, ["role", "results"], "qa")
    if len(d["results"]) != len(functions):
        raise GateError(f"qa: {len(d['results'])} results for {len(functions)} planned functions")
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    art = norm(artifact_text)
    for r in d["results"]:
        _need(r, ["id", "result", "evidence"], "qa result")
        if r["result"] not in ("pass", "fail"):
            raise GateError(f"qa {r['id']}: bad result")
        if r["result"] == "pass" and (not r["evidence"] or norm(r["evidence"]) not in art):
            raise GateError(f"qa {r['id']}: pass evidence is not a verbatim quote from the artifact")
    return d


def validate_qa_fix_acceptance(d: dict, fix_reviews: list) -> dict:
    _need(d, ["accept", "reasons"], "qa_fix_acceptance")
    if not isinstance(d["accept"], bool):
        raise GateError("accept must be boolean")
    if d["accept"]:
        for r in fix_reviews:
            if r["verdict"] != "approve" and any(i["severity"] != "minor" for i in r["issues"]):
                raise GateError("accept=true requires every fix review to approve or raise only minor issues")
    return d

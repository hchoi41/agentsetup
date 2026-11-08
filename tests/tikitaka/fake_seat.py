"""Deterministic fake seat for engine tests. Reads the prompt on stdin, answers by role.
Scenario (JSON file in $TIKITAKA_FAKE_SCENARIO) keys:
  review_rounds_with_issue: int (default 1)  - reviewers raise one fixable issue for this many builds
  bad_review: bool                            - reviewers return garbage
  disagree: [decision types]                  - judge disagrees on these
  qa_fail_first: bool                         - first QA run fails Q1
  sleep: seconds                              - sleep before answering
  env_dump: path                              - write the env var names seen
State file next to the scenario counts builds."""
import json
import os
import re
import sys
import time

prompt = sys.stdin.read()
sc_path = os.environ.get("TIKITAKA_FAKE_SCENARIO", "")
sc = json.load(open(sc_path, encoding="utf-8")) if sc_path else {}
state_path = sc_path + ".state.json" if sc_path else None
state = json.load(open(state_path, encoding="utf-8")) if state_path and os.path.exists(state_path) else {"builds": 0, "qa": 0}
if sc.get("env_dump"):
    with open(sc["env_dump"], "a", encoding="utf-8") as f:
        f.write(json.dumps(sorted(os.environ)) + "\n")
if sc.get("sleep"):
    time.sleep(sc["sleep"])
head = prompt.splitlines()[0] if prompt else ""


def out(result):
    print(json.dumps({"result": result, "usage": {"input_tokens": len(prompt) // 4, "output_tokens": 50}}))


def fence(d):
    return "```json\n" + json.dumps(d) + "\n```"


if "BUILDER" in head:
    state["builds"] += 1
    out(f"<<<ARTIFACT\n# Artifact\n```sh\nrun me\n```\nFUNCTION-ALPHA is present and documented in full here.\n"
        f"TAIL-AFTER-CODE-BLOCK\nbuild {state['builds']}\nARTIFACT>>>\n<<<CHANGES\ninitial build\nCHANGES>>>")
elif "ACCURACY REVIEWER" in head or "COLD REVIEWER" in head:
    if sc.get("bad_review"):
        out("not json at all")
    else:
        pref = re.search(r"Ids: prefix (\S+?-)\s", prompt).group(1)
        role = re.search(r'"role": "([a-z_0-9]+)"', prompt).group(1)
        seat = re.search(r'"seat": "([^"]+)"', prompt).group(1)
        issues = []
        if state["builds"] <= sc.get("review_rounds_with_issue", 1):
            issues = [{"id": pref + "1", "location": "s1", "problem": "p", "evidence": "e", "fix": "f",
                       "severity": "major", "fixable_in_loop": True}]
        out(fence({"role": role, "seat": seat, "model": "fake", "artifact_version": "v", "verdict": "revise" if issues else "approve",
                   "issues": issues}))
elif "JUDGE in a tikitaka loop" in head:
    rev = prompt.split("===== REVIEWS (gate-validated) =====")[1].split("=====")[0]
    ids = re.findall(r'"id": "([AC]-[^"]+)"', rev)
    out(fence({"artifact_version": "v", "plan_assessment": "ok", "process_assessment": "ok", "output_assessment": "ok",
               "items": [{"id": i, "decision": "improve", "reason": "valid"} for i in ids],
               "judge_items": [], "improvements": [f"[{', '.join(ids)}] fix it"] if ids else [],
               "improvement_necessary": "yes" if ids else "no"}))
elif "proposes a major decision of type" in prompt.splitlines()[0]:
    t = re.search(r"type '([a-z_]+)'", head).group(1)
    agree = t not in sc.get("disagree", [])
    out(fence({"type": t, "proposal": {"by": "orchestrator", "action": "a", "reasons": "r"}, "role": "judge", "model": "fake",
               "agree": agree, "reasons": "fine" if agree else "no"}))
elif head.startswith("You are QA/QC"):
    state["qa"] += 1
    n = len(re.findall(r"^Q\d+\. ", prompt, re.M))
    fail_first = sc.get("qa_fail_first") and state["qa"] == 1
    res = [{"id": f"Q{i + 1}", "result": "fail" if (fail_first and i == 0) else "pass",
            "evidence": "FUNCTION-ALPHA is present and documented in full here.", "note": ""} for i in range(n)]
    out(fence({"role": "qa_qc", "seat": "x", "artifact_version": "v", "results": res}))
elif "A bounded fix" in head:
    out(fence({"accept": True, "reasons": "ok", "model": "fake"}))
elif "AUDITOR" in head:
    out("# Audit\n\nFake audit with evidence links: verdicts/, reviews/.")
else:
    out("ok: " + head[:60])
if state_path:
    json.dump(state, open(state_path, "w", encoding="utf-8"))

"""The tikitaka loop (charter §4, §6, §7). Control flow, gates, access and records are code (D1)."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import access, gates, preflight, profiles, prompts
from .config import Job, double_check_pair_present, extension_roles
from .runlog import RunLog
from .seats import SeatResult, run_seat

MAX_REINVOKE = 2


class Escalation(Exception):
    def __init__(self, reason: str, decision_needed: str):
        super().__init__(reason)
        self.reason, self.decision_needed = reason, decision_needed


class Engine:
    def __init__(self, job: Job, run_dir: Path, *, hub: Path | None = None, session_id: str | None = None,
                 cli_overrides: list | None = None, dispatch=run_seat, check_tools: bool = True):
        self.job, self.hub, self.session_id = job, hub, session_id
        self.log = RunLog(run_dir)
        self.dispatch = dispatch
        self.check_tools = check_tools
        self.cli_overrides = cli_overrides or []
        self.spec = job.spec_path.read_text(encoding="utf-8")
        self.sources = "\n\n".join(f"--- {p.name} ---\n{p.read_text(encoding='utf-8')}" for p in job.source_paths)
        self.run_round = 0
        self.v = 0
        self.verdicts: list = []
        self.status = "running"
        self.stopped_by_cap = False
        self.replacements: list = []
        self.unfixable: list = []
        self.rejected_items: list = []

    # ---- plumbing ------------------------------------------------------------------------------------
    def _call(self, role_name: str, prompt: str, tag: str, milestone: str = "-", rnd="-") -> SeatResult:
        role = self.job.roles[role_name]
        env = access.worker_env(role_name, self.overrides)
        res = self.dispatch(role, prompt, out_prefix=self.log.dir / "calls" / f"{tag}_{role.seat}", env=env)
        self.log.add_tokens(role_name, role.seat, milestone, rnd, res.tokens, res.usage)
        self.log.event(f"{role_name} ({role.seat}, {role.cli}/{role.model}) -> {tag}: rc={res.returncode}, tokens={res.tokens}")
        if res.incomplete:
            self.log.add_cutoff(role_name, role.seat, res.cutoff_reason,
                                "user request" if "user-set" in res.cutoff_reason else "situation (runner)")
        return res

    def _cap_reached(self) -> bool:
        return self.job.token_cap is not None and self.log.total_tokens() >= self.job.token_cap

    def _artifact_path(self, v: int) -> Path:
        return self.log.dir / "artifacts" / f"v{v}_{self.job.artifact_name}"

    def _agree(self, decision_type: str, key: str, action: str, reasons: str, evidence: str) -> dict:
        judge = self.job.roles["judge"]
        model = f"{judge.model}/{judge.effort}"
        for attempt in range(MAX_REINVOKE + 1):
            res = self._call("judge", prompts.agreement(decision_type, action, reasons, evidence, model), f"agree_{key}_{attempt}")
            try:
                d = gates.validate_agreement(gates.extract_json(res.text), decision_type=decision_type)
                if res.incomplete:
                    raise gates.GateError("judge output incomplete (cut off)")
                rec = self.log.add_agreement(d, key)
                self.log.event(f"agreement {key}: agree={d['agree']} (seq {rec['seq']})")
                return rec
            except gates.GateError as e:
                self.log.event(f"agreement {key} invalid (attempt {attempt + 1}): {e}")
        raise Escalation(f"judge returned no valid agreement for {key}", f"decide {decision_type} yourself")

    def require_gate(self, key: str):
        if not self.log.gate_passed(key):
            raise Escalation(f"gate {key} has no valid judge agreement", f"approve or revise {key}")

    def consult(self, step: str, question: str) -> str:
        res = self._call("judge", f"You are the JUDGE, consulted by the orchestrator on step '{step}'. Answer briefly.\n\n{question}",
                         f"consult_{len(list((self.log.dir / 'consultations').glob('*.json'))) + 1}")
        self.log.add_consultation(step, question, res.text, self.job.roles["judge"].model)
        return res.text

    # ---- stages --------------------------------------------------------------------------------------
    def _extensions(self, stage: str, inputs: str) -> str:
        outs = []
        for role in extension_roles(self.job, stage):
            res = self._call(role.name, prompts.extension(role, inputs), f"ext_{stage}_{role.name}")
            self.log.write_json(f"extensions/{stage}_{role.name}.json", {"output": res.text, "hands_off_to": role.hands_off_to})
            outs.append(f"[{role.name} -> {role.hands_off_to}]\n{res.text}")
        return "\n\n".join(outs)

    def _plan(self, research: str) -> str:
        j = self.job
        roles = "\n".join(f"- {n}: seat {r.seat} ({r.cli} {r.model} {r.effort})".rstrip() for n, r in j.roles.items())
        plan = (f"# Plan — {j.name}\n\n## Brief\n{j.brief}\n\n## Planned functions (QA/QC checks these)\n"
                + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(j.planned_functions))
                + f"\n\n## Milestones\n" + ("\n".join(f"- {m}" for m in j.milestones) or "- none (one loop, m1)")
                + f"\n\n## Roles\n{roles}\n\n## Loop\nmax_iterations={j.max_iterations} per milestone loop; token_cap={j.token_cap}\n"
                + (f"\n## Research\n{research}\n" if research else ""))
        (self.log.dir / "plan.md").write_text(plan, encoding="utf-8")
        return plan

    def _build(self, plan: str, prev: str | None, improvements: list | None, milestone: str, rnd: int, extra: str):
        res = self._call("builder", prompts.builder(self.job, plan, self.spec, self.sources, prev, improvements, extra),
                         f"{milestone}_r{rnd}_build", milestone, rnd)
        art = re.search(r"<<<ARTIFACT\s*\n(.*)\nARTIFACT>>>", res.text, re.S)
        chg = re.search(r"<<<CHANGES\s*\n(.*)\nCHANGES>>>", res.text, re.S)
        if not art or res.incomplete:
            raise Escalation("builder returned no complete artifact block", "inspect calls/*build* and decide")
        self.v += 1
        self._artifact_path(self.v).write_text(art.group(1), encoding="utf-8")
        (self.log.dir / "artifacts" / f"v{self.v}.changes.md").write_text(chg.group(1) if chg else "(no change note)", encoding="utf-8")
        self.log.event(f"builder wrote v{self.v} (change note artifacts/v{self.v}.changes.md)")
        return art.group(1)

    def _review_one(self, role_name: str, artifact: str, milestone: str, rnd: int) -> dict | None:
        role = self.job.roles[role_name]
        version = f"v{self.v}"
        tries = [role_name] + ([role.extra["replacement"]] if role.extra.get("replacement") else [])
        for idx, rn in enumerate(tries):
            r = self.job.roles[role_name] if idx == 0 else _replacement_role(role, role.extra["replacement"])
            for attempt in range(MAX_REINVOKE + 1):
                p = (prompts.accuracy(r, version, artifact, self.spec, self.sources) if role_name.startswith("accuracy")
                     else prompts.cold(r, version, artifact, self.spec))
                if idx == 0:
                    res = self._call(role_name, p, f"{milestone}_r{rnd}_{role_name}_{attempt}", milestone, rnd)
                else:
                    env = access.worker_env(role_name, self.overrides)
                    res = self.dispatch(r, p, out_prefix=self.log.dir / "calls" / f"{milestone}_r{rnd}_{role_name}_repl{attempt}_{r.seat}", env=env)
                    self.log.add_tokens(role_name, r.seat, milestone, rnd, res.tokens, res.usage)
                try:
                    if res.incomplete:
                        raise gates.GateError("output incomplete (cut off) — never passed to the judge as a full review")
                    d = gates.validate_review(gates.extract_json(res.text), role=role_name, seat=r.seat)
                    self.log.write_json(f"reviews/{milestone}_r{rnd}_{role_name}_{r.seat}.json", d)
                    self.log.event(f"gate PASS review {role_name}/{r.seat}: verdict={d['verdict']}, issues={len(d['issues'])}")
                    return d
                except gates.GateError as e:
                    self.log.event(f"gate REJECT review {role_name}/{r.seat} attempt {attempt + 1}: {e}")
            if idx == 0 and len(tries) > 1:
                self.replacements.append({"role": role_name, "from": role.seat, "to": role.extra["replacement"]["seat"]})
                self.log.event(f"seat replacement for {role_name}: {role.seat} -> {role.extra['replacement']['seat']}")
        return None

    def _review_set(self, milestone: str, rnd: int) -> list:
        dc = access.double_check_state(self.job, session_id=self.session_id, run_round=self.run_round)
        self.log.write_json("double_check.json", {**dc, "milestone": milestone, "round": rnd})
        names = ["accuracy_reviewer", "cold_reviewer"]
        if dc["active"]:
            if not double_check_pair_present(self.job):
                raise Escalation("double-check is active but the job config has no accuracy_reviewer_2/cold_reviewer_2",
                                 "add the second reviewer pair or turn double-check off")
            names += ["accuracy_reviewer_2", "cold_reviewer_2"]
        return names

    # ---- main ----------------------------------------------------------------------------------------
    def run(self) -> str:
        j = self.job
        try:
            self._preflight()
            research = ""
            if "researcher" in j.roles:
                research = self._call("researcher", f"You are the RESEARCHER. Research best practices and technical details for this brief; "
                                                    f"cite a source for every claim.\n\n{j.brief}", "research").text
            research += self._extensions("before_plan", j.brief)
            plan = self._plan(research)
            self._agree("plan_approval", "plan_approval", "approve plan.md", "plan written from the job brief",
                        f"PLAN:\n{plan}\n\nSPEC:\n{self.spec}")
            self.require_gate("plan_approval")
            artifact = None
            for k, milestone in enumerate(j.milestones or ["m1"], start=1):
                mk = f"m{k}"
                artifact = self._milestone_loop(plan, mk, artifact)
                self._agree("milestone", f"milestone_{mk}", f"declare milestone {mk} ({milestone}) reached",
                            f"loop ended; final version v{self.v}; stopped_by_cap={self.stopped_by_cap}",
                            f"ARTIFACT v{self.v}:\n{artifact}\n\nLAST VERDICT:\n{json.dumps(self.verdicts[-1] if self.verdicts else {}, ensure_ascii=False)}")
                self.require_gate(f"milestone_{mk}")
            artifact = self._qa_phase(artifact)
            self._agree("finish", "finish", f"finish: adopt v{self.v}", "QA/QC passed",
                        f"ARTIFACT v{self.v}:\n{artifact}\n\nQA:\n{(self.log.dir / 'qa.json').read_text(encoding='utf-8')}")
            self.require_gate("finish")
            self.status = "finished"
        except Escalation as e:
            self.status = "escalated"
            (self.log.dir / "ESCALATION.md").write_text(
                f"# Escalation\n\n**Failure:** {e.reason}\n\n**What was tried:** see process.md\n\n**Decision needed:** {e.decision_needed}\n",
                encoding="utf-8")
            self.log.event(f"ESCALATED: {e.reason}")
        except preflight.PreflightError as e:
            self.status = "preflight_failed"
            self.log.event(f"PREFLIGHT FAILED: {e}")
        finally:
            self._audit()
        return self.status

    def _preflight(self):
        self.overrides = access.active_overrides(project=self.job.project, session_id=self.session_id, hub=self.hub,
                                                 cli_overrides=self.cli_overrides)
        rejected = access.reject_agent_overrides(self.job.raw, self.log.dir)
        self.log.write_json("overrides.json", {"accepted": self.overrides, "rejected": rejected})
        for r in rejected:
            self.log.event(f"override from {r['source']} REJECTED (agent-writable source)")
        if self.job.double_check_source.startswith("ignored"):
            self.log.event("job-config double_check ignored: not from a user-owned source")
        if self.check_tools:
            for cli in sorted({r.cli for r in self.job.roles.values() if r.cli in ("codex", "claude")}):
                preflight.resolve_application(cli)
            used = profiles.resolve_job_profiles(self.job)
            self.log.event("profiles: " + ", ".join(f"{r}={p.name}" for r, p in sorted(used.items())))
            preflight.run_logins(self.job.raw.get("require_logins", []), self.job.raw.get("login_commands"))
        self.log.event("preflight ok")

    def _milestone_loop(self, plan: str, mk: str, artifact: str | None) -> str:
        j = self.job
        improvements = None
        for rnd in range(1, j.max_iterations + 1):
            if self._cap_reached():
                self.stopped_by_cap = True
                self.log.event(f"stopped by cap: token_cap {j.token_cap} reached before round {rnd}")
                break
            self.run_round += 1
            extra = self._extensions("build", plan)
            artifact = self._build(plan, artifact, improvements, mk, rnd, extra)
            names = self._review_set(mk, rnd)
            with ThreadPoolExecutor(max_workers=len(names)) as ex:
                reviews = list(ex.map(lambda n: self._review_one(n, artifact, mk, rnd), names))
            missing = [n for n, r in zip(names, reviews) if r is None]
            if missing:
                raise Escalation(f"incomplete review set in {mk} r{rnd}: no valid review from {missing}",
                                 "re-run, replace the seat, or stop the run as 'stopped: incomplete reviews'")
            gates.check_unique_ids(reviews)
            self._extensions("review", artifact)
            verdict = self._verdict(plan, artifact, reviews, mk, rnd)
            self.rejected_items += [i for i in verdict["items"] if i["decision"] == "reject"]
            ids_fix = {i["id"]: i["fixable_in_loop"] for r in reviews for i in r["issues"]}
            self.unfixable += [i for r in reviews for i in r["issues"] if not i["fixable_in_loop"]]
            if verdict["improvement_necessary"] == "no":
                self.log.event(f"judge: improvement_necessary=no -> exit loop at {mk} r{rnd}")
                return artifact
            if rnd == j.max_iterations:
                self.stopped_by_cap = True
                self.log.event(f"stopped by cap: max_iterations={j.max_iterations} reached at {mk}")
                return artifact
            improvements = verdict["improvements"]
        return artifact

    def _verdict(self, plan, artifact, reviews, mk, rnd) -> dict:
        process = (self.log.dir / "process.md").read_text(encoding="utf-8")
        p = prompts.judge(f"v{self.v}", rnd, self.job.max_iterations, plan, process, artifact, self.spec, self.sources, reviews,
                          self.verdicts)
        for attempt in range(MAX_REINVOKE + 1):
            res = self._call("judge", p, f"{mk}_r{rnd}_judge_{attempt}", mk, rnd)
            try:
                if res.incomplete:
                    raise gates.GateError("judge output incomplete (cut off)")
                d = gates.validate_verdict(gates.extract_json(res.text), reviews)
                self.verdicts.append(d)
                self.log.write_json(f"verdicts/{mk}_r{rnd}_judge.json", d)
                self.log.event(f"gate PASS verdict {mk} r{rnd}: improvement_necessary={d['improvement_necessary']}, driving={len(d['_driving'])}")
                return d
            except gates.GateError as e:
                self.log.event(f"gate REJECT verdict {mk} r{rnd} attempt {attempt + 1}: {e}")
        raise Escalation(f"no valid judge verdict in {mk} r{rnd}", "inspect the judge outputs and decide")

    def _qa(self, artifact: str, tag: str) -> dict:
        role = self.job.roles["qa_qc"]
        for attempt in range(MAX_REINVOKE + 1):
            res = self._call("qa_qc", prompts.qa(self.job.planned_functions, f"v{self.v}", artifact, role.seat), f"{tag}_{attempt}")
            try:
                d = gates.validate_qa(gates.extract_json(res.text), functions=self.job.planned_functions, artifact_text=artifact)
                self.log.write_json(f"{tag}.json", d)
                return d
            except gates.GateError as e:
                self.log.event(f"gate REJECT {tag} attempt {attempt + 1}: {e}")
        raise Escalation("no valid QA/QC result", "run QA/QC manually")

    def _qa_phase(self, artifact: str) -> str:
        q = self._qa(artifact, "qa")
        fails = [r for r in q["results"] if r["result"] == "fail"]
        self.log.event(f"QA/QC v{self.v}: {len(q['results']) - len(fails)} pass, {len(fails)} fail")
        if not fails:
            return artifact
        rec = self._agree("qa_failure_handling", "qa_failure_handling", "handle QA failures with one bounded fix",
                          "failures look fixable within the failed functions", json.dumps(q, ensure_ascii=False))
        if not rec["agree"]:
            raise Escalation("QA/QC failed and the judge did not agree to handle it automatically", "decide how to fix the QA failures")
        failed_txt = [f"[QA-{r['id']}] fix only this failed function: {self.job.planned_functions[int(r['id'][1:]) - 1]} ({r.get('note', '')})"
                      for r in fails]
        plan = (self.log.dir / "plan.md").read_text(encoding="utf-8")
        artifact = self._build(plan, artifact, ["Bounded fix: change only what the failed functions need; no new functions; "
                                                "do not touch passing functions."] + failed_txt, "qafix", 1, "")
        names = ["accuracy_reviewer"]
        if access.double_check_state(self.job, session_id=self.session_id, run_round=self.run_round)["active"]:
            names.append("accuracy_reviewer_2")
        fix_reviews = [self._review_one(n, artifact, "qafix", 1) for n in names]
        if any(r is None for r in fix_reviews):
            raise Escalation("bounded fix has no valid accuracy review", "review the fix yourself")
        for r, n in zip(fix_reviews, names):
            self.log.write_json(f"reviews/qafix_{n}_{r['seat']}.json", r)
        judge = self.job.roles["judge"]
        res = self._call("judge", prompts.qa_fix_acceptance(q, fix_reviews, artifact, judge.model), "qafix_acceptance")
        try:
            acc = gates.validate_qa_fix_acceptance(gates.extract_json(res.text), fix_reviews)
        except gates.GateError as e:
            raise Escalation(f"invalid qa_fix_acceptance: {e}", "decide whether to accept the fix") from None
        self.log.write_json("verdicts/qafix_judge.json", acc)
        if not acc["accept"]:
            raise Escalation("judge did not accept the bounded fix", "decide how to fix the QA failures")
        q2 = self._qa(artifact, "qa_rerun")
        if any(r["result"] == "fail" for r in q2["results"]):
            raise Escalation("QA/QC failed again after the bounded fix", "decide; no second bounded fix is allowed")
        (self.log.dir / "qa.json").write_text(json.dumps(q2, ensure_ascii=False, indent=1), encoding="utf-8")
        return artifact

    def _digest(self) -> str:
        files = sorted(str(p.relative_to(self.log.dir)).replace("\\", "/") for p in self.log.dir.rglob("*")
                       if p.is_file() and "calls" not in p.parts)
        return json.dumps({
            "job": self.job.name, "status": self.status, "final_version": f"v{self.v}", "stopped_by_cap": self.stopped_by_cap,
            "verdicts": [{k: v for k, v in d.items() if k != "_driving"} for d in self.verdicts],
            "rejected_items": self.rejected_items, "unfixable_issues": self.unfixable, "replacements": self.replacements,
            "cutoffs": self.log.cutoffs, "tokens_total": self.log.total_tokens(), "tokens": self.log.tokens,
            "process": (self.log.dir / "process.md").read_text(encoding="utf-8"), "files": files,
        }, ensure_ascii=False, indent=1)

    def _audit(self):
        digest = self._digest()
        (self.log.dir / "audit_digest.json").write_text(digest, encoding="utf-8")
        text = ""
        if "auditor" in self.job.roles and self.job.roles["auditor"].cli != "internal":
            try:
                text = self._call("auditor", prompts.auditor(digest), "audit").text.strip()
            except Exception as e:  # the audit must exist even if the auditor seat fails
                self.log.event(f"auditor seat failed: {e}")
        if not text:
            text = _fallback_audit(json.loads(digest))
        (self.log.dir / "AUDIT.md").write_text(text + "\n", encoding="utf-8")


def _replacement_role(role, rep: dict):
    from .config import Role
    return Role(role.name, rep["seat"], rep["cli"], rep.get("model", ""), rep.get("effort", ""), role.stage,
                extra={k: v for k, v in rep.items() if k not in ("seat", "cli", "model", "effort")})


def _fallback_audit(d: dict) -> str:
    lines = [f"# Audit — {d['job']}", "", f"**Outcome:** {d['status']}; final version {d['final_version']}; "
             f"stopped by cap: {d['stopped_by_cap']}; tokens: {d['tokens_total']}.", "", "## Judge verdicts"]
    for v in d["verdicts"]:
        lines.append(f"- {v.get('artifact_version')}: improvement necessary = {v['improvement_necessary']} "
                     f"({len(v['items'])} items) — evidence: verdicts/")
    rej = [f"- {i['id']}: {i['reason']}" for i in d["rejected_items"]] or ["- none"]
    cut = [f"- {c}" for c in d["cutoffs"]] or ["- none"]
    lines += ["", "## Rejected items", *rej, "", "## Cut-offs", *cut,
              "", "## Evidence files", *[f"- {f}" for f in d["files"]]]
    return "\n".join(lines)

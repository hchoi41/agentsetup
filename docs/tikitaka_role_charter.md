# Tikitaka Role Charter — Max Agent System

**Version:** v5 (2026-11-08) · adopted by the owner; implemented by `orchestrate/tikitaka/` (v5.3.0)
**Execution label:** D1 — role outputs are model judgment; control flow, gates, access and records are code.

---

## 1. Purpose and scope

Tikitaka is the iterative recursive improvement loop of the Max Agent System. This charter defines its roles, what each role receives and returns, who it hands off to, and the rules that code must enforce. Roles are **inputs**: each job chooses which roles it uses and which agent/model fills each one. The role set is open.

Out of scope: tool-setup fixes, lessons, and release (criteria sections 3–7) — they implement this charter but are specified elsewhere.

## 2. Role assignment (job input)

A job config assigns roles. No role-to-model binding is hard-coded.

```json
"roles": {
  "orchestrator":         { "seat": "<alias>", "cli": "<claude|codex|copilot|...>", "model": "<id>", "effort": "<level>" },
  "builder":              { ... },
  "accuracy_reviewer":    { ... },
  "cold_reviewer":        { ... },
  "judge":                { ... },
  "qa_qc":                { ... },
  "auditor":              { ... },
  "researcher":           { ... },   // optional
  "accuracy_reviewer_2":  { ... },   // double-check pair, required when double-check is active
  "cold_reviewer_2":      { ... },
  "<new_role>":           { ..., "stage": "<stage>", "receives": [...], "hands_off_to": "<role>" }
},
"double_check": { "enabled": false, "duration": { "type": "iterations", "n": 2 } },
"max_iterations": 3,
"token_cap": null
```

- **Mandatory roles:** orchestrator, builder, accuracy_reviewer, cold_reviewer, judge, qa_qc, auditor. **Optional:** researcher; the double-check pair (`accuracy_reviewer_2`, `cold_reviewer_2`), required only when double-check is active. Code rejects a config that is missing a mandatory role.
- **Seat aliases** are unique across all role entries; code rejects a config with duplicate aliases.
- **Counters:** `r` = round within the current milestone loop (resets to 1 at each milestone). `v` = artifact version, run-wide and increasing across milestones; the final deliverable is the last `v`.
- **`max_iterations`:** the cap on `r` (builder rounds per milestone loop; per run when the plan lists no milestones). **`token_cap`:** total tokens across all roles for the whole run. Both are checked only between role calls; they never interrupt a running role. `null` = no cap.
- **One agent, several roles:** allowed (e.g., orchestrator also builds). When the agent holding the orchestrator role also fills a worker role, that worker role runs in a separate process launched under the §5 worker rules; the orchestrator process never performs worker work directly. The cold reviewer always runs in a fresh process with no shared session history; the judge runs as a separate invocation from any reviewer it judges.
- **Extension mechanism:** each role entry may carry `stage` (one of `before_plan`, `build`, `review`, `verdict`, `after_loop`, `final`), `receives` and `hands_off_to`. The loop dispatches every configured role at its stage. Built-in defaults: researcher `before_plan`, builder `build`, reviewers `review`, judge `verdict`, qa_qc `after_loop`, auditor `final`. A new role needs only its §3 entry and a config entry with a stage; the loop is not rewritten.

## 3. Roles

Each role: **does** · **receives** · **returns** · **hands off to**.

### 3.1 Orchestrator / PM
- **Does:** plans the whole workflow; runs the job; saves tokens by routing work; holds live-environment access (§5); declares milestones reached (§4); asks the judge for help on any step (logged, §6.4); proposes every major decision (§6.1).
- **Receives:** user brief, job config, live environment (by default only this role).
- **Returns:** `plan.md` (saved in the run directory; lists the planned functions and the milestones, zero or more), task briefs for each role, the narrative section of `process.md` (§8), all hand-offs.
- **Hands off to:** every role. To the cold reviewer it passes only the artifact file and the spec. Major decisions go to the judge for agreement (§6).
- **Note:** may be a lower-capacity model. The design never requires it to do the heavy building or judging; where intelligence matters, it consults the judge.

### 3.2 Researcher (optional)
- **Does:** researches best practices and technical details.
- **Receives:** a research brief from the orchestrator.
- **Returns:** findings with a source for each claim (URL, file path, or document reference).
- **Hands off to:** orchestrator, which passes the relevant findings to the builder and into the plan.

### 3.3 Builder
- **Does:** builds, and revises on the judge's improvement list.
- **Receives:** plan, task brief, researcher findings (via orchestrator), the judge's `improvements` list (r ≥ 2) as its sole work list, data the orchestrator provides.
- **Returns:** the artifact `artifacts/v<v>` and a separate change note `artifacts/v<v>.changes.md` (what changed and why, per item id). The artifact itself carries no change history, reviewer references or reasoning.
- **Hands off to:** reviewers (via orchestrator).

### 3.4 Accuracy reviewer
- **Does:** checks the artifact for correctness against its spec and sources.
- **Receives:** the artifact, the spec, the sources it must match.
- **Returns:** a review in the review schema (§7).
- **Hands off to:** judge.

### 3.5 Cold reviewer
- **Does:** reviews with no prior context.
- **Receives:** the artifact and the spec it must satisfy — **only**. No build history, change notes, builder reasoning, earlier versions, or other reviews.
- **Returns:** a review in the review schema (§7).
- **Hands off to:** judge.

### 3.6 Double-checker (optional)
- **Does:** adds a second, parallel accuracy reviewer (`accuracy_reviewer_2`) and a second, parallel cold reviewer (`cold_reviewer_2`).
- **Receives:** the same inputs as the first reviewer of the same kind, delivered independently.
- **Returns:** a review in the review schema (§7).
- **Hands off to:** judge, which receives all four reviews.
- **Rules:** switched on per job. The four reviewers run independently and never see each other's reviews. The second pair may use the same model as the first or a different one; the choice is recorded in the run directory.
- **Duration (user-set):**
  - *N iterations* — N counts builder rounds run-wide (across milestones) from `start_round`, then switches off.
  - *Session* (non-project chat) — ends when that session ends; never inherited by a new session.
  - *Project* (project chat) — persists across sessions of the same project until `project-complete`; never inherited by another project.
- **Where the setting lives, and precedence:** an N-iterations setting lives in the job config `double_check` block, where `duration.type` accepts only `iterations` (other values are rejected at config load). A session setting lives in the session settings file; a project setting in the project settings file. An active session or project setting can enable or disable double-check for the current job and overrides `double_check.enabled`. If an active setting turns double-check on for a job whose config lacks `accuracy_reviewer_2`/`cold_reviewer_2`, dispatch fails with a logged error — no silent skip.
- **Provenance:** the job-config `double_check` block is accepted only from a user-owned source under the same provenance rule as §5 overrides; a block from an agent-written source is ignored and logged.
- **Scope record (`double_check.json`):** scope (iteration count, session ID, or project slug), source, `start_round` (the run-wide round at which the setting first became active — 1 unless the user enables it mid-run through a settings file), and the current `r` and run-wide round. Code checks the record at every review dispatch.

### 3.7 Judge
- **Does:** evaluates, assimilates and consolidates. Judges four things: the **plan**, **how it was built** (`process.md`), **what was built** (output), and **the reviews**. Decides which improvements to make and whether any improvement is necessary. May raise its own items (judge-originated, §7). Helps the orchestrator whenever asked (logged, §6.4). Gives or withholds agreement on major decisions (§6). Accepts or rejects a QA fix (§6.3).
- **Receives:** plan, `process.md`, the artifact, the complete set of valid reviews for the round (both, or all four with double-check), prior judge verdicts.
- **Returns:** a verdict in the judge schema (§7): per item `improve | keep | reject` with a reason, its own items if any, the `improvements` list for the builder, and `improvement_necessary: yes|no`.
- **Hands off to:** orchestrator (next round or exit).
- **Note:** usually the highest-intelligence model in the job.

### 3.8 QA/QC
- **Does:** checks that the final deliverable functions as planned. Runs once per final deliverable version, after the loop (and the last milestone) ends. Not part of recursive improvement. The single re-run after a §6.3 fix is the only exception (approved amendment).
- **Receives:** final artifact, plan (the list of planned functions).
- **Returns:** `qa.json` — per planned function: pass/fail with evidence.
- **Hands off to:** orchestrator and judge. A fail blocks "finished"; see §6.3.

### 3.9 Auditor
- **Does:** explains the process and the outcome to human users; makes complex information accessible.
- **Receives:** the full run directory.
- **Returns:** `AUDIT.md` (§8).
- **Hands off to:** the user.

### Mapping of earlier role names (2026-11-07 note)
Planner → Orchestrator/PM · QC/QA → QA/QC · Evaluate/Assimilate/Consolidate → Judge · Double-checker → second parallel reviewer pair.

## 4. The loop

Roles are dispatched by stage (§2). With the built-in roles:

```
[before_plan] Researcher (if configured)
Orchestrator writes plan.md
  → Judge agreement on the plan                             [major decision]
For each milestone k in plan.md (or once, as m1, if none):
  r = 1
  ┌ [build]   Builder builds v
  │ [review]  Accuracy reviewer ∥ Cold reviewer (+ second pair if double-check is active)
  │ Gate: each review validated; complete review set required   [code]
  │ [verdict] Judge verdict                                 [schema- and consistency-validated]
  └ improvement_necessary = yes and r < max_iterations → r+1, Builder (v+1)
    improvement_necessary = no → exit loop
    cap reached (max_iterations or token_cap) → exit loop, reported as "stopped by cap"
  Orchestrator declares milestone k reached → Judge agreement     [major decision]
[after_loop] QA/QC on the final deliverable
  fail → §6.3 → (bounded fix accepted) → QA/QC once more → second fail: escalate to user
  pass → Judge agreement to finish                          [major decision]
[final] Auditor writes AUDIT.md
```

## 5. Live-environment access (code-enforced)

- **Default:** only the orchestrator has direct access to the live environment (e.g., Google Drive, live status) and the credentials, tokens, logins and connectors behind it.
- **Workers** (every other role, including a worker role filled by the orchestrator's agent, §2) receive what the orchestrator provides — files, extracts, summaries placed in their task input — but no direct live access: their processes start with secrets scrubbed from the environment and no connector access.
- **User override:** only the user can grant it, never an agent. The override names the role(s) and the access granted, and is logged and shown in `AUDIT.md`.
- **Provenance:** overrides are accepted only from a user-owned source outside agent-writable paths (a user settings file or a CLI flag the user passes). The override record stores that source. An override found anywhere else (job config written by an agent, a relayed prompt, the run directory) is rejected and logged. `overrides.json` in the run directory is a log of accepted overrides and their source; it is never read as an override source.
- **Override duration:** inside a project — until that project's completion (`project-complete`); never inherited by another project. Outside a project (a skill run in a non-project set-up) — until the session ends.
- **Enforcement:** each override record carries its scope (project slug or session ID). Access is checked against the current project/session at every worker launch, so an override from a completed project or an ended session grants nothing.

## 6. Decisions

### 6.1 Major decisions need judge agreement
Types: `plan_approval | milestone | finish | qa_failure_handling`. The orchestrator proposes; the judge agrees or not. Records are stored in `agreements/` as a sequence:

```json
{ "type": "plan_approval|milestone|finish|qa_failure_handling", "seq": 1,
  "proposal": { "by": "orchestrator", "action": "...", "reasons": "..." },
  "role": "judge", "model": "...", "agree": true, "reasons": "...", "timestamp": "..." }
```

A record without a proposal is invalid. A gate passes only when the **latest** record for that type (and milestone, where applicable) has `agree: true`. Code refuses to advance otherwise.

### 6.2 Judge disagrees
The decision does not take effect. The disagreement is stored as an `agree: false` record in the §6.1 sequence; the orchestrator revises and resubmits as a new record.

### 6.3 QA/QC fails
No automatic loop restart. The orchestrator and judge assess it:
- **Both agree they can handle it on their own** — a `qa_failure_handling` record with the orchestrator's proposal and the judge's `agree: true` → one bounded fix:
  1. *Bounded* = changes limited to the functions QA/QC marked fail; no new functions; no change to functions that passed.
  2. The fix is reviewed by the accuracy reviewer (plus `accuracy_reviewer_2` when double-check is active) in the §7 review schema, stored as `reviews/qafix_<role>_<seat>.json`. The cold reviewer is not required on this path — an explicit exemption from the complete-review-set rule (§7), because the fix is scoped to failed functions, not a new artifact.
  3. The judge returns a `qa_fix_acceptance` record (not a loop verdict): `verdicts/qafix_judge.json` `{accept: true|false, reasons, model, timestamp}`. `accept: true` is valid only if every fix review has verdict `approve` or only minor issues.
  4. On `accept: true`, QA/QC re-runs once on the new version. Finishing still requires its own `finish` agreement.
  5. Any other outcome — `accept: false`, or a second QA/QC fail — escalates to the user. There is no second bounded fix.
- **Otherwise, or if user feedback is required** → escalate to the user and wait. The escalation states the failure, what was tried, and the decision needed.

### 6.4 Judge consultations
The orchestrator may consult the judge on any step, not only major decisions. Every consultation is logged as `consultations/<n>.json` `{step, question, answer, model, timestamp}` and summarised in `AUDIT.md`.

## 7. Schemas and loop rules (code-enforced)

**Issue ids**, unique across all reviews and the verdict of a round (checked at the gate; unique seat aliases, §2, keep ids distinct across the second reviewer pair):
- reviewer items: `<role letter>-<seat>-<n>` — `A` accuracy, `C` cold (e.g., `A-rev1-1`, `C-rev2-2`);
- judge-originated items: `J-<n>` (e.g., `J-1`).

**Review schema** (all reviewers):
```json
{ "role": "accuracy_reviewer|cold_reviewer|accuracy_reviewer_2|cold_reviewer_2", "seat": "...", "model": "...",
  "milestone": "m1", "round": 1, "artifact_version": "v1",
  "verdict": "approve|revise",
  "issues": [ { "id": "A-rev1-1", "location": "§x / line", "problem": "...", "evidence": "...",
                "fix": "...", "severity": "blocking|major|minor", "fixable_in_loop": true } ] }
```

**Judge schema:**
```json
{ "milestone": "m1", "round": 1, "artifact_version": "v1",
  "plan_assessment": "...", "process_assessment": "...", "output_assessment": "...",
  "items": [ { "id": "A-rev1-1", "decision": "improve|keep|reject", "reason": "...",
               "fixable_override": null } ],
  "judge_items": [ { "id": "J-1", "location": "...", "problem": "...", "fix": "...",
                     "severity": "blocking|major|minor", "fixable_in_loop": true } ],
  "improvements": [ "[A-rev1-1, C-rev2-4] concrete edit ..." ],
  "improvement_necessary": "yes|no" }
```

**Item decisions:** `improve` — act on it. `keep` — the artifact stays as it is (the issue is noted but not worth changing). `reject` — the issue is wrong, a duplicate, or out of scope; not acted on, listed in `AUDIT.md` with the reason. (approved amendment: keep/improve/reject.)

Rules:
- **Validate before use:** a review or verdict that fails its schema, or is empty/truncated, is rejected and logged — never consumed.
  - *Invalid review:* re-invoke that reviewer (a fresh process for a cold reviewer) up to **2** times. If still invalid, the orchestrator may replace the seat (a different model, same inputs, fresh process; logged as a replacement). If the required review set is still incomplete, the round does **not** reach the judge: the run pauses and escalates to the user. It ends as "stopped: incomplete reviews" (with `AUDIT.md`) only if the user so decides, or if the job config declares no user channel (headless run).
  - *Invalid verdict:* re-requested; the loop never advances without a valid verdict.
- **Complete review set:** both reviews (all four with double-check active) — except the §6.3 QA-fix path.
- **Count only fixable issues:** iteration is driven only by items with `fixable_in_loop: true` that are either reviewer items decided `improve` or judge-originated items. The judge may override a reviewer's `fixable_in_loop` flag with a stated reason (`fixable_override`, recorded in the verdict). An `improve` decision on an item whose effective `fixable_in_loop` is false is invalid and fails the gate. Unfixable issues are reported separately (to the orchestrator, and in `AUDIT.md`).
- **Improvements list:** each entry starts with the item id(s) it resolves; the list is the builder's sole work list. The gate fails if: an entry has no id; an id in the list is not a driving item of this round; a driving item appears in no entry; or the list is empty while `improvement_necessary` is `yes`.
- **Verdict consistency:** `improvement_necessary: yes` is valid only if at least one item drives iteration; `no` only if none does. An inconsistent verdict fails the gate and is re-requested.
- **Stop:** the loop exits on `improvement_necessary: no`, or when `max_iterations` or `token_cap` is reached between role calls (reported as "stopped by cap").
- **Cut-off prevention rule:** by default no role is cut off mid-task (e.g., headless runners must not kill a reviewer's background work); caps never interrupt a running role. Cut-off is the exception — only on the user's explicit request or in an emergency. Every cut-off is logged with its trigger and reason; the affected output is marked incomplete and never passed to the judge as a full review.
- **Token log:** tokens per role per round are logged in `tokens.json`.

## 8. Auditability

Every run ends with `AUDIT.md`, written for a human reader:
- what was planned; what each role did; what the reviewers found; what the judge decided and why (including rejected items); consultations; overrides and cut-offs; seat replacements; incomplete-review stops; the final outcome and QA/QC result;
- each claim links to its evidence in the run directory (plan, reviews, verdicts, gate results, agreement records);
- plain language, terms explained, no unexplained internal IDs.

**`process.md`:** the record fields — the sequence of role invocations per milestone and round, builder change-note pointers, gate results, re-invocations and seat replacements, cut-offs, token totals per round — are appended by code. The orchestrator may add only a clearly separated narrative section for deviations from the §4 order and their reasons.

**Run directory** (`_orchestrate/<job>_<ts>/`): `plan.md`, `process.md`, `artifacts/v<v>`, `artifacts/v<v>.changes.md`, `reviews/m<k>_r<r>_<role>_<seat>.json`, `verdicts/m<k>_r<r>_judge.json`, `reviews/qafix_<role>_<seat>.json`, `verdicts/qafix_judge.json`, `agreements/*.json`, `consultations/*.json`, `double_check.json`, `overrides.json`, `cutoffs.json`, `tokens.json`, `qa.json`, `AUDIT.md`.

## 9. Consistency with existing docs

- `orchestrate/README.md` describes **locked** roles (Codex maker + red-team, Copilot reviewer #2 + bug tracker, Claude conductor + referee-on-disagreement). This charter replaces that with **assignable** roles and a judge that rules every round, not only on disagreement. The AGENTS.md roles line and `orchestrate/README.md` are updated **in the same change that lands this charter** (adoption, not later implementation). Replacement AGENTS.md roles line: *"Roles are assigned per job from the tikitaka charter (builder · accuracy reviewer · cold reviewer · judge · orchestrator/PM · QA/QC · auditor; researcher and double-checker optional); no model is bound to a role."*
- `00.ABOUT/orchestration_protocol.md` (coordination rules: digests, delegation contract, no recursive delegation, one writable owner) remains valid and applies to every role here. Its "decompose by context boundary, not job title" rule is satisfied because the reviewer and judge roles exist for different context boundaries (cold vs. full context), not titles.
- `escalation` tiers in `orchestrate/README.md` remain available; §6.3 is the QA/QC-specific escalation.

## 10. Required tests

The implementation must include at least:
1. Double-check duration — one test each for N iterations (counted run-wide across milestones), session, and project scope (including: a session setting is not inherited by a new session; a project setting is not inherited by another project; a project setting survives a new session of the same project); a `double_check` block from an agent-written source is ignored.
2. Workers have no direct live-environment access (no secrets, tokens or connectors in a worker process), including a worker role filled by the orchestrator's agent.
3. Override scope — (a) a new project does not inherit a prior project's override; (b) a new session does not inherit a prior non-project session's override; (c) `project-complete` clears the project's overrides.
4. Override provenance — an override from an agent-writable source is rejected.
5. Judge-agreement gates — a run that tries to skip plan approval, milestone or finish is refused; a record without an orchestrator proposal is refused; a gate whose latest record is `agree: false` is refused.
6. Gate rules — invalid review rejected and re-invoked; incomplete review set never reaches the judge; inconsistent verdict rejected; duplicate issue ids rejected; duplicate seat aliases rejected; improvements entry without an id, with a non-driving id, or missing a driving item rejected; empty improvements with `yes` rejected.
7. Cut-off prevention — a reviewer's long-running work is not killed by the runner by default; caps take effect only between role calls; a cut-off is logged and its output marked incomplete.
8. Extension — a role added by config with a `stage` is dispatched without code changes to the loop.

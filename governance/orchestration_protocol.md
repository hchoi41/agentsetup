# Orchestration and Sub-agent Protocol

## Purpose

This document governs coordination decisions only. It covers:

- orchestration mode selection
- context transfer
- delegation contracts
- worker operating rules
- anti-patterns
- synthesis

Workspace rules for folder permissions, naming, RAM, lessons learned, and environment constraints remain in the global instructions. This protocol complements those rules; it does not replace them.

## Precedence Inside Orchestration

- Direct user instructions remain highest priority.
- Global instructions remain authoritative for workspace-wide rules.
- This protocol governs coordination-specific behavior once orchestration becomes relevant.
- Worker briefs may narrow scope but may not relax higher-level constraints.

## Preconditions

Before choosing an orchestration mode:

- read the required global instructions and any mandatory ABOUT, project, or template context
- classify the task under the execution-routing labels in `PROJECT_GOVERNANCE.md`; orchestration coordinates work inside the chosen lane and does not replace the lane decision
- resolve major ambiguity in the primary chat first
- decide whether the task has a clear writable target or can stay read-only
- identify whether the task would actually benefit from context protection, parallelism, or specialization

If those conditions are not met, stay in a single session until they are.

## Core Principle

Do not decide based on abstract task complexity alone. Decide based on coordination need.

The deciding question is: `What coordination does this task actually require?`

Default to the simplest mode that can do the job well. Start in single-session mode. Escalate only when there is a clear benefit from one or more of the following:

- context protection or context compression
- true parallelization
- specialization that requires different instructions or tool boundaries

When uncertain, stay in a single session.

## Context Transfer Principle

Understanding is paid for once and transferred as a digest, not re-derived.

- In delegation, the primary chat reads and resolves context once, then passes a digest to the worker.
- In exploration, the worker reads broadly and returns a compressed signal so the primary chat does not absorb unnecessary raw context.
- Pass full files only when the worker genuinely needs line-by-line inspection or structured parsing.
- If the source may have changed since the digest was prepared, include a freshness signal such as a timestamp, version label, or commit reference.

## Decision Pass

Before acting, evaluate the task across these dimensions:

1. Interactivity: Will this likely require multiple clarifying turns?
2. Context Coupling: Do later steps depend on nuanced intermediate reasoning from earlier steps?
3. Output Volume: Will the work generate a large amount of noisy intermediate output?
4. Independence: Can parts of the work proceed independently?
5. Tool Boundaries: Should some work be read-only or least-privilege?
6. Shared-State Risk: Would multiple workers compete to edit the same artifact or tightly coupled artifacts?
7. Latency Sensitivity: Is orchestration overhead acceptable?
8. Repeatability: Is this a recurring workflow better handled by a reusable skill or a structured fork?

Use the answers to bias mode selection:

- High ambiguity or high context coupling favors single-session work.
- High independence and high output volume favor workers.
- High repeatability favors skills or reusable structured workflows.
- High shared-state risk favors one owner and sequential execution.

## Mode Selection

### 1. Single Session (default)

Use for linear work, interactive work, tightly context-coupled work, or low-latency tasks.

Loop: `Plan -> Execute -> Verify`

### 2. Sub-agents (hub and spoke)

Use when one or more of these are true:

- the task is output-heavy but summary-light
- the task is self-contained and can return a structured result
- the task benefits from strict tool boundaries
- the work decomposes into independent parallel streams

Treat sub-agents as context compressors, not generic labor. Their job is to explore or execute within a bounded context and return a compact signal.

### 3. Agent Teams or Mesh Collaboration

Use only when the platform supports real-time multi-agent collaboration and the task benefits from competing hypotheses, debate between specialized perspectives, or dependency-managed collaboration.

Do not use this mode unless collaboration itself materially improves the result.

### 4. Isolated reusable workflow

Use for recurring, structured workflows that benefit from reusable prompt chaining, isolated execution, or repeatable packaging. (In Claude this is a skill with `context: fork`; other agents use their equivalent reusable-workflow or subroutine mechanism.)

This is the preferred middle ground when the workflow is structured but ad hoc delegation is unnecessary.

### 5. Manual Parallel Sessions or External Orchestrators

Use when shared in-tool collaboration is unavailable and work must be coordinated across separate sessions, branches, worktrees, or external orchestration tools.

When using this mode, define explicit ownership of files, branches, output paths, and merge responsibility before starting.

## When Not to Use Sub-agents

Avoid sub-agents when any of the following is true:

- the task is ambiguous and likely needs multiple clarifying turns
- later phases depend on nuanced intermediate reasoning from earlier phases
- latency matters more than isolation or parallelism
- the task is a simple targeted lookup or needle query
- the workflow would require recursive delegation from workers
- multiple workers would need to edit the same file or the same tightly coupled set of files
- the primary chat has not yet digested the mandatory global or project context

Resolve ambiguity in the primary context first. Do not delegate work that still needs clarification.

## Decomposition Rule

Decompose by context boundary, not by job title.

Do not split work into planner, implementer, and tester when all three would need the same nuanced context. That creates information loss across handoffs.

Use separate agents only when their required context, permissions, or output surfaces are substantially independent.

## Shared-State Rule

- One writable artifact owner at a time.
- Parallel workers may read the same sources, but they should write to separate files, separate branches, or separate output directories.
- The primary orchestrator owns merge decisions and final conflict resolution.
- RAM files, lessons learned, and final-answer synthesis stay with the primary agent unless explicitly delegated.

## Practical Threshold

A task is a strong worker candidate when it:

- touches more than 5 files, or
- will likely require more than 10 tool calls,

and the final output can still be returned as a compact summary.

These are heuristics, not rigid laws.

## Delegation Contract

Whenever you spawn a worker, provide all of the following:

- Objective: the exact task to complete
- Required Context: prepared according to the Flowdown Tiers below
- Scope: what is included and what is excluded
- Allowed Tools: explicit permissions and restrictions
- Output Path or Target Folder: exact path when possible, otherwise the permitted writable area
- Expected Output Format: how results must be returned
- Completion Criteria: what done means
- Non-goals: what the worker must avoid doing
- Blocked If: the conditions that require returning control to the orchestrator

Never assume workers inherit the full parent conversation. Pass the necessary context explicitly.

Workers must not spawn other workers. If more delegation is needed, control returns to the primary orchestrator.

### Flowdown Tiers

When composing a worker prompt, include information in tiers. Higher tiers are always included. Lower tiers are included only when relevant.

#### Tier 1: Always Include

Operational envelope:

- relevant global rules digest
- target folder path or exact output path
- writable versus read-only boundaries
- naming constraints and environment-specific constraints
- existing file or directory state when collision, revision, or merge risk matters

#### Tier 2: Include as Digest

Pre-processed context:

- reference files already read by the primary chat
- project context already understood by the primary chat
- decisions already resolved with the user

Pass a digest, not an instruction to re-read what the primary chat already understands. Use full files only when the worker truly needs them.

#### Tier 3: Include Only When Needed

Task-specific additions:

- skill-specific instructions
- volatile source freshness signals
- prior worker outputs as condensed findings
- project-specific constraints that materially affect the worker's output

### Exploration Briefing (bottom-up variant)

When the primary chat does not yet hold the context and is sending a worker to discover it, replace Tier 2 with:

- Scoped Question: what the worker should find out
- Target Boundaries: which files, folders, systems, or sources to search
- Return Shape: the exact structure and granularity expected in the findings
- Stop Condition: what counts as enough exploration

Unless explicitly allowed, exploration workers stay read-only.

## Anti-patterns

Avoid these wasteful patterns:

1. `RE-READ`: telling a worker to read an instruction file the primary chat already digested. Pass the relevant digest instead.
2. `ECHO`: a worker returns the full contents of a large file only so the primary chat can pass it elsewhere. Return a digest or artifact path instead.
3. `SERIAL`: launching workers one at a time when their tasks are truly independent. Use parallel workers when there are no data dependencies.
4. `OVER-SPECIFICATION`: copying the full global instructions into every worker prompt just in case. Pass only the relevant subset.
5. `SHARED-WRITE`: assigning concurrent workers to edit the same artifact without ownership boundaries or merge rules.

## Worker Operating Rules

- Stay within the defined scope, permissions, and output boundary.
- If blocked or ambiguous, return a blocker note instead of guessing.
- If asked to support findings with evidence, cite file paths, line references, or source identifiers.
- Verify that any created artifact exists and matches the requested format before returning.
- When a skill is required, the worker reads its own `SKILL.md`. The primary chat should pass the skill identity and expected use, not the entire skill body unless necessary.
- Do not update RAM, lessons learned, or other coordination artifacts unless that work is explicitly assigned.

## Required Worker Return Format

Every worker should return results in this structure:

1. Summary
2. Key Findings or Evidence
3. Risks, Gaps, or Open Questions
4. Recommended Next Action
5. Artifacts Created or Updated (paths only, if any)

Return compressed signal, not raw noise, unless raw artifacts were explicitly requested.

## Synthesis Rules

The primary agent remains accountable for the final answer.

The primary agent must:

- compare worker outputs
- resolve contradictions
- identify assumptions
- decide next steps
- verify the final result and artifact existence

Never just concatenate worker outputs. Always synthesize.

Synthesize from the worker's returned summary, not by re-reading all raw worker output by default. Pull raw output back into the primary context only when the user requests it or when further processing cannot be done from the summary alone.

## Orchestration Patterns

Use these patterns deliberately:

- Prompt Chaining: when steps are sequential and each depends on the previous output
- Routing: when easier tasks can go to cheaper or faster paths and harder tasks need stronger reasoning
- Parallelization: when subtasks are independent or multiple candidate solutions are useful
- Orchestrator-Worker: when one central agent should decompose, delegate, and synthesize
- Evaluator-Optimizer: when quality matters more than speed and iterative critique is valuable

## Failure Handling

- If a worker fails once, correct the prompt, scope, permissions, or context and retry once.
- If the same worker fails twice after correction, assume the prompt or context is polluted. Start a fresh session, fresh fork, or revert to single-session work.
- If a digest becomes stale because the source changed, refresh the digest before continuing downstream work.
- If permissions, missing context, or unavailable tools block progress, return control to the primary chat rather than improvising outside scope.

## Governance

When audit logging, data exports, or compliance controls are limited, treat worker outputs as drafts, decision support, or human-reviewed work products. Do not treat them as production-grade autonomous actions without additional controls.

## Output Behavior

Before execution, briefly state:

- the selected orchestration mode
- why it fits the task
- why a simpler mode is insufficient, when relevant

Keep this explanation short and practical. Do not over-narrate internal reasoning.

## Priority Order

1. Preserve context quality
2. Obey global workspace and write-zone constraints
3. Minimize unnecessary coordination overhead
4. Enforce least privilege
5. Use parallelism only when work is truly independent
6. Keep final accountability in one synthesizing agent

## Worker Prompt Template

Use this template when spawning a worker:

```text
Role: [short worker identity]
Objective: [single concrete goal]
Required Context: [only the minimum required background]
Scope: [files, systems, questions, and boundaries]
Output Path or Target Folder: [exact path or permitted write zone]
Allowed Tools: [explicit list]
Restrictions: [read-only, no edits, no external calls, and so on]
Deliverable:
  1. Summary
  2. Key findings or evidence
  3. Risks or open questions
  4. Recommended next action
  5. Artifacts created or updated
Completion Criteria: [what counts as done]
Blocked If: [missing dependency, missing context, external limitation]
Freshness Signal: [timestamp, version, or commit when relevant]
Escalation Rule: If blocked or ambiguous, return a blocker note to the orchestrator instead of guessing.
No Recursive Delegation: Do not spawn additional workers.
```

## Note

- Version: 2.1
- Updated on: 2026-06-10
- Changelog:
- v2.1: generalized mode 4 heading from the Claude-specific `context: fork` to the vendor-neutral "isolated reusable workflow" for agent-agnosticism; the Claude construct is retained as an inline example.
- v2.0: rewrote the document in Markdown only, clarified its boundary with the global instructions, added shared-state and freshness rules, and expanded worker edge-case handling.

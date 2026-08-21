# Orchestrate — generalized multi-LLM "tiki-taka" conductor

`Orchestrate.ps1` (hub root) generalizes the build-only `Orchestrate-Build.ps1` into a
**config-driven** loop: the roles stay locked, the **gate is pluggable**, and each **job**
is a JSON file. The same proven loop drives **code** or a **document**.

## Locked roles (the "set in advance")

| LLM | Default model | Standing role |
|---|---|---|
| **Codex** | gpt-5.5 · xhigh | **Maker** (writes/fixes) + **Red-team** (review #1) |
| **Copilot** | auto | **Reviewer #2** + tie-break input + end-of-run **bug tracker** |
| **Claude** | opus-4.8 · max | **Conductor** (runs this) + **Referee** (tie-break only, on disagreement) |

`config.roles` may override a role's `model`/`effort` (or swap a `cli`), but not the choreography.

## The loop (per task)

```
MAKE (Codex) → GATE (deterministic) → RED-TEAM (Codex) + REVIEW (Copilot)
            → REFEREE (Claude, only if the two disagree) → APPROVE or FIX (≤ maxFix) → next task
                                                          → BUG TRACKER (Copilot) at the end
```

## The pluggable gate (what made docs possible)

The gate has two layers:

1. **Deterministic** — `config.gate.checks[]` (or per-task `task.gate.checks[]`). Each is a native
   command; **pass = all exit 0**. This is the objective oracle.
   - `{ "type":"pytest", "path":"tests\\memory" }` → `python -m pytest <path> -q` (code)
   - `{ "type":"command", "command":"pwsh -File checks\\Check-Additive.ps1 -Base … -New …" }` → any command (docs/other)
   - *no checks* → no deterministic gate; quality rests on the review triad + rubric
2. **Judgment** — the Codex/Copilot/Claude review triad, judged against `config.rubric`. Unchanged from the build conductor.

Code jobs lean on layer 1 (pytest can't be argued with). Doc jobs lean on layer 2, with
`Check-Additive.ps1` as a deterministic **drift tripwire** that proves the edit was additive
(no locked decision/line was deleted or reworded).

## Config schema (`orchestrate/configs/*.json`)

| Key | Meaning |
|---|---|
| `name` | job id (names the run dir) |
| `hub` | hub root (resolved from the runtime environment) |
| `workProduct` | `code` \| `doc` \| other (labeling) |
| `briefRef` | the spec/plan/handoff the maker reads |
| `artifacts` | paths/globs the reviewers see (the work under review) |
| `rubric` | the approval bar reviewers judge against |
| `gate.checks[]` | deterministic checks (job-level default; tasks may override) |
| `roles` | per-role `cli`/`model`/`effort` overrides |
| `maxFix` | fix-rounds before halting for a human (default 2) |
| `taskInstructionTemplate` | optional; `{id}`/`{title}` substituted when a task has no `instruction` |
| `tasks[]` | `{ id, title, instruction?, gate? }` |

## Run it

```powershell
# 1) check the CLIs are on PATH + authed (no LLM spend)
.\Orchestrate.ps1 -Config orchestrate\configs\design_doc_retrofit.json -Preflight

# 2) print the resolved plan — roles, gate, per-task instructions (no LLM spend)
.\Orchestrate.ps1 -Config orchestrate\configs\design_doc_retrofit.json -DryRun

# 3) run for real (SPENDS Codex/Copilot credits + Claude tokens)
.\Orchestrate.ps1 -Config orchestrate\configs\design_doc_retrofit.json

# one task only
.\Orchestrate.ps1 -Config orchestrate\configs\design_doc_retrofit.json -Task A
```

Run artifacts (per-task make/fix logs, gate output, review JSON, `BUGS.md`) land in
`_orchestrate\<name>_<timestamp>\`.

## Bundled jobs

- `configs\v1a_core_build.json` — backward-compat: the original 12-task code build, now driven by the generalized conductor (idempotent re-run; pytest gate).
- `configs\design_doc_retrofit.json` — first **document** job: retrofit spec v9→v10 and arch v7→v8 to the design-doc principles, additive-only, additive-diff gate + "strong" rubric.

## Escalation (chain of command)

By default a job runs in **full delegation** (`escalation.mode: "auto"`) — the agents run to a final output and never ping you; unresolved items land in `BUGS.md`. Set `escalation.mode: "tiered"` to have the conductor **escalate to a human** when a blocker is severe enough. Each non-approval, the reviewers/referee classify a `blocker`; the conductor escalates when its severity ≥ your configured rank, writing **`ESCALATION.md`** (rank, reason, the decision you must make, artifact paths) and halting with **exit 3** in an `escalated` state.

| `escalation.level` (rank) | escalates on `blocker` | sev | fires |
|---|---|---|---|
| `commander` | `unachievable` — output can't be achieved as specified | 4 | immediately |
| `secretary` | `design_change` — plan doesn't work in reality, needs redesign | 3 | after ≥ `minIterationsForDesign` (default 3) iterations |
| `chief_of_staff` | `scope_change` — needs a meaningful re-scope | 2 | when flagged |
| `general` | `unresolved` — agents + referee can't converge | 1 | at fix-loop exhaustion |

**Cumulative** — a rank also catches everything more severe (`chief_of_staff` gets scope_change + design_change + unachievable). `auto` catches nothing.

```json
"escalation": { "mode": "tiered", "level": "secretary", "minIterationsForDesign": 3 }
```
`secretary` needs `maxFix >= 2` so ≥3 iterations are reachable. Exit codes: `0` all approved · `1` halt (below-threshold / auto) · `3` escalated to human.

## Notes

- **Gitless** by hub policy — no `git` steps anywhere.
- A **real run spends credits/tokens**. Always `-Preflight` then `-DryRun` first.
- `Orchestrate-Build.ps1` is retained unchanged; `Orchestrate.ps1` supersedes it for new jobs.
- **Copilot adapter feeds the prompt via a temp file** (`-p "Read the file at '<tmp>' …"`), not as a command-line argument. Codex/Claude take the prompt on **stdin**, but Copilot takes `-p <arg>`, and a large review prompt (full artifact snapshot) blows the Windows ~32 KB command-line limit — surfacing as a `node.exe` launch failure. The file hand-off keeps the arg tiny at any prompt size. All review CLIs also run with a real `*> file` redirect + `try/catch` so one CLI's failure degrades to a referee call instead of crashing the run.

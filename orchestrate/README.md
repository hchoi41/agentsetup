# Orchestrate — generalized multi-LLM "tiki-taka" conductor

`orchestrate/Orchestrate.ps1` generalizes the earlier build-only conductor into a
**config-driven** loop: the roles stay locked, the **gate is pluggable**, and each **job**
is a JSON file. The same proven loop drives **code** or a **document**.

## Locked roles (the "set in advance")

| LLM | Model selection | Standing role |
|---|---|---|
| **Codex** | Job configuration or engine default | **Maker** (writes/fixes) + **Red-team** (review #1) |
| **Copilot** | Job configuration or engine default | **Reviewer #2** + tie-break input + end-of-run **bug tracker** |
| **Claude** | Job configuration or engine default | **Conductor** (runs this) + **Referee** (tie-break only, on disagreement) |

`config.roles` may override a role's `model`/`effort` (or swap a `cli`), but not the choreography.

## Choose agents on this computer

New installations begin with agent selection pending. Use `Setup-AgentCLIs.ps1` to
choose one or more agents, or explicitly choose none. The machine-local choice is saved
in `00.ABOUT/AGENT_CLI_SETUP.json`; installing a CLI does not authenticate it or prove
that a paid job can run.

Both engines check that file in their installation root and the resolved job hub, including
ancestor hubs. Pending, none, or malformed state blocks real runs, preflight and previews
before provider commands or run writes. None disables multi-agent orchestration even when
CLIs happen to be installed. Every configured role must use a selected CLI, including the
conditional referee and final bug tracker. When multiple hub choices apply, all must permit
the job. Neither engine silently changes a provider, model, or role mapping.

The default job needs Codex, GitHub Copilot CLI and Claude Code. Codex, Copilot and the
existing experimental Gemini adapter can fill the maker role; Claude cannot. OpenCode's
skill folders support individual use, but no OpenCode conductor adapter exists. Selecting
one CLI therefore does not make the default multi-provider job runnable; use individual
work or explicitly configure supported roles and compatible models.

Existing hubs with no selection state retain legacy behavior. Upgrades preserve saved
choices. A copied legacy engine without the shared guard can run only if neither root nor
its ancestors has state; a missing guard beside an opted-in installation is a repair error.
The shared guard checks selection only. Verify executable availability and authentication
separately, then review the actual job before spending credits. `Orchestrate2.ps1 -Capabilities`
only reports its contract and remains available without a configured hub or agent selection.

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
# Run from the installed hub root. Pass HubRoot because the engine lives in orchestrate/.
# 1) inspect CLI version probes (no LLM spend; authentication is NOT checked)
.\orchestrate\Orchestrate.ps1 -HubRoot $PWD.Path -Config orchestrate\configs\_template_code_job.json -Preflight

# 2) print the resolved plan — roles, gate, per-task instructions (no LLM spend)
.\orchestrate\Orchestrate.ps1 -HubRoot $PWD.Path -Config orchestrate\configs\_template_code_job.json -DryRun

# 3) run for real (SPENDS Codex/Copilot credits + Claude tokens)
.\orchestrate\Orchestrate.ps1 -HubRoot $PWD.Path -Config orchestrate\configs\my_job.json

# one task only
.\orchestrate\Orchestrate.ps1 -HubRoot $PWD.Path -Config orchestrate\configs\my_job.json -Task A
```

Run artifacts (per-task make/fix logs, gate output, review JSON, `BUGS.md`) land in
`_orchestrate\<name>_<timestamp>\`.

The v1 `-Preflight` output is informational: it runs `--version`, does not check login,
and is not a complete readiness gate (including native exit-status validation). A successful
preflight command does not establish access to a model or a valid subscription. Complete the
selected providers' sign-in steps separately; onboarding never runs a paid prompt to test them.

## Bundled jobs

- `configs\_template_code_job.json` is the only bundled job configuration. Copy it to a new
  job file, set the actual plan, artifacts, gate and tasks, then preview before running.
- Machine-specific and historical job configurations are deliberately excluded.

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

## Engine v2

`Orchestrate2.ps1` ships beside v1. v1 is unchanged, so legacy configs keep running on it. v2 uses structured argv gates rather than shell strings, a pre-created run root, strict adapter outcomes so a failed vendor cannot read as success, and an explicit severity contract. Real v2 runs require a consent receipt, so v2 is launcher-driven rather than hand-run. It has never been run for real: its 51/51 tests use fakes only, with zero vendor spend.

## Notes

- **Gitless** by hub policy — no `git` steps anywhere.
- A **real run spends credits/tokens**. Always `-Preflight` then `-DryRun` first.
- `Orchestrate-Build.ps1` is retained unchanged; `Orchestrate.ps1` supersedes it for new jobs.
- **Copilot adapter feeds the prompt via a temp file** (`-p "Read the file at '<tmp>' …"`), not as a command-line argument. Codex/Claude take the prompt on **stdin**, but Copilot takes `-p <arg>`, and a large review prompt (full artifact snapshot) blows the Windows ~32 KB command-line limit — surfacing as a `node.exe` launch failure. The file hand-off keeps the arg tiny at any prompt size. All review CLIs also run with a real `*> file` redirect + `try/catch` so one CLI's failure degrades to a referee call instead of crashing the run.

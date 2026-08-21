# agentsetup

The published, version-controlled agent setup for my machines. One place that always holds the
latest state; any new computer gets the whole environment from here in one command.

> **This repo is public and contains no personal data.** It is assembled from an explicit
> allowlist (`payload_manifest.json`) and every push runs a secret/PII gate. Working notes,
> memory, transcripts, and career material live only on the local hubs and never enter this repo.

---

## Install on a new machine

```powershell
git clone https://github.com/hchoi41/agentsetup.git C:\000.agentsetup_repo
cd C:\000.agentsetup_repo
.\Get-AgentSetup.ps1 -Destination C:\000.myhub -DryRun    # see the plan
.\Get-AgentSetup.ps1 -Destination C:\000.myhub            # do it
```

Or just tell an agent: *"install the latest agent setup at `C:\000.myhub`"* — the
`agentsetup-sync` skill covers the whole procedure.

**Requires:** git, PowerShell 7+. Optional: python 3.11+, node, and the `codex` / `claude` /
`copilot` CLIs (needed to *run* the orchestrate conductor, not to install).

### The one thing git cannot carry

`.claude/`, `.agents/`, `.opencode/`, `.github/`, and `microsoft_copilot/` are **NTFS junctions**
pointing back at `skills/`. Junctions have no representation in git. `Get-AgentSetup.ps1` calls
`scripts/sync_agent_adapters.ps1` to rebuild them locally from `AGENT_TARGETS.json`. Skip that
step and the hub looks installed but no agent can see the skills.

---

## Layout

| Path | What |
|---|---|
| `Bootstrap.ps1` / `Get-AgentSetup.ps1` | Install onto a machine |
| `Publish-AgentSetup.ps1` | Push a new state up from the OneDrive hub |
| `payload_manifest.json` | The allowlist. Single source of truth for what publishes. |
| `AGENT_TARGETS.json` | Which agents get the skills, and how (junction vs generated adapter) |
| `governance/` | `PROJECT_GOVERNANCE.md`, `CLAUDE.md`, orchestration + ingestion protocols |
| `template/` | The v5 folder scaffold a new hub is built from |
| `skills/` | Canonical unpacked skill sources — each a `SKILL.md` |
| `scripts/` | `sync_agent_adapters.ps1`, memory layer, hub health checks |
| `orchestrate/` | The multi-LLM conductor: engine, README, generic gate, diagrams |
| `tests/` | Memory-layer pytest — the default code gate for orchestrate jobs |
| `global_instructions/` | Standalone global fallback for when no project folder is mounted |

---

## The orchestrate conductor

`orchestrate/Orchestrate.ps1` runs a config-driven "tiki-taka" loop with locked roles:

| LLM | Role |
|---|---|
| **Codex** | Maker (writes/fixes) + Red-team (review #1) |
| **Copilot** | Reviewer #2 + tie-break input + end-of-run bug tracker |
| **Claude** | Conductor + Referee (fires only on disagreement) |

```
MAKE → GATE (deterministic) → RED-TEAM + REVIEW → REFEREE (on tie) → APPROVE or FIX → next
```

The hub root resolves at run time — `config.hub` → `-HubRoot` → `$env:AGENT_HUB_ROOT` → the
script's own folder. No machine-specific paths are baked in. See `orchestrate/README.md`.

---

## Contributing to this repo

Don't hand-edit files here. The flow is one-directional:

```
local hub  →  980.agents_setup (OneDrive)  →  this repo  →  GitHub
```

Change the hub, promote to `agents_setup`, then run `Publish-AgentSetup.ps1`. Editing the repo
directly means the next publish overwrites you.

Commit convention: `{type}({scope}): {summary}` — `feat` `fix` `docs` `chore` `refactor` `plan` `research`.

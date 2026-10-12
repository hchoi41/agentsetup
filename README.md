# agentsetup

The published, version-controlled agent setup for my machines. One place that always holds the
latest install payload; a Windows computer can install it into a local hub.

> **This public repo excludes personal workspace data.** It is assembled from an explicit
> allowlist (`payload_manifest.json`). Before publication, the complete prepared payload must
> pass source/inventory verification, relevant tests, and a secret/PII/path scan. Working notes,
> memory, transcripts, and career material are excluded from the payload.

---

## Install on a Windows computer

The installer creates `01.PROJECT` and `10.OPERATION`, the operations dashboard and switch,
empty project controls, and the five core workflows: `start-project`, `project-complete`,
`cloud-sync`, `operationalize`, and `projectify`. It installs procedures and local adapters;
it does not sign in to AI services, install account-wide skills, schedule jobs, or copy any
personal projects. Project INPUT permits create/edit; deleting input files or folders still
requires the owner's explicit permission. Operation INPUT remains read-only.

Use a local folder you can write, outside OneDrive. No administrator elevation is requested.
**PowerShell 7+ (`pwsh`) is required.** Git is needed for the download/update route below;
the extracted-ZIP route needs no Git. Python 3.11+, Node, and AI CLIs are separate optional
dependencies for the workflows that use them. On a managed company PC, obtain those tools
through its approved software channel; this installer does not change execution policy.

Download and preview first:

```powershell
$hubPath = Join-Path $env:LOCALAPPDATA 'AgentSetup/hub'
irm https://raw.githubusercontent.com/hchoi41/agentsetup/main/Bootstrap.ps1 -OutFile "$env:TEMP/Bootstrap.ps1"
pwsh -NoProfile -File "$env:TEMP/Bootstrap.ps1" -Destination $hubPath -DryRun
pwsh -NoProfile -File "$env:TEMP/Bootstrap.ps1" -Destination $hubPath
```

The default cache is `%LOCALAPPDATA%/AgentSetup/repo`; `-CacheRoot` selects another local
folder. Existing cache changes are refused, never reset or discarded. A fresh-cache dry run
does not download files and therefore cannot validate the remote payload yet.

For a downloaded repository ZIP, extract it into a separate local folder, then run from its root:

```powershell
$hubPath = Join-Path $env:LOCALAPPDATA 'AgentSetup/hub'
pwsh -NoProfile -File ./Get-AgentSetup.ps1 -SourceRoot $PWD.Path -Destination $hubPath -DryRun
pwsh -NoProfile -File ./Get-AgentSetup.ps1 -SourceRoot $PWD.Path -Destination $hubPath
```

`-SourceRoot` installs those exact local bytes and does not fetch from GitHub. It is also the
mode used by the install verification script. Use `-CopyAdapters` if junctions are unsuitable:
native skill surfaces become copies, and rerunning the installer refreshes generated copies.
The Copilot prompt and self-contained document surfaces are generated in either mode.

No persistent environment setting changes by default. If wanted, add `-SetUserEnvironment`
to set the user-level `AGENT_HUB_ROOT`; otherwise pass the hub path to your agent/workflow.
The installer verifies that all five core skills resolve through the three native adapters.
The initial `AGENTS.md` is a template: fill its project identity before starting work.

### Upgrades and local customization

Legacy workspaces containing `01.INPUT`, `11.OUTPUT_ROUGH`, or `03.OUTPUT_FINAL` must use
their governed migration first. This installer refuses to create a mixed layout and never
moves existing user work.

For an existing project-first hub, rerun the installer. Payload files, including `AGENTS.md`,
`00.ABOUT/CLAUDE.md`, adapter configuration, and the operations dashboard, update when their
bytes still match the previous install ledger. Locally edited or created payload/source files
are kept and reported. Local project controls are never replaced. No automatic text merge is
performed. Generated adapter documents and copied skill surfaces are regenerated on each run,
including locally edited generated files. Customize the canonical `skills/` and `AGENTS.md`
sources instead. Edits made directly to those generated documents or copied skill surfaces
are not preserved.
`-Force` explicitly replaces conflicting payload files and keeps their originals under
`13.MEMORY_HDD/agentsetup_install_<timestamp>/`; it still does not reset local project controls.
Review any preserved customization before using upgraded workflows.

### Local verification

From the repository root, use a new test folder outside the repository:

```powershell
pwsh -NoProfile -File ./tests/install/Test-Install.ps1 -FixtureRoot "$env:TEMP/agentsetup-install-review"
```

The test leaves its disposable installation, logs, and `validation.json` for inspection.
It checks the new layout and skills, repeat installs, customization preservation, forced-change
backups, refusal cases, and unchanged user environment. It does not test your actual company
computer, configure accounts, or publish anything.

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
| `tests/` | Memory-layer pytest and disposable Windows installer verification |
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
script's own folder. This package stores the engine inside `orchestrate/`, so pass the hub
explicitly when no environment setting is used. From the installed hub root:

```powershell
.\orchestrate\Orchestrate.ps1 -HubRoot $PWD.Path -Config .\orchestrate\configs\_template_code_job.json -DryRun
```

The bundled config is a starting template; fill in the task and plan before a real run.
No machine-specific paths are baked in. See `orchestrate/README.md`.

---

## Contributing

**Outside contributors: pull requests are welcome.** Keep changes focused on `skills/`,
`scripts/`, `orchestrate/`, `governance/`, or `template/` content, and never include real
personal data or machine-specific paths. A maintainer reviews the PR, ingests it into the
private curated sources, and republishes — your merged commits stay in the history, followed by
a pipeline commit that formalizes them. The publisher's **DRIFT GUARD** refuses to publish over
commits it didn't push, so a merged PR cannot be silently overwritten.

**Maintainers:** change the canonical sources first. The flow is

```
local hub → private curated store → reviewed release branch → pull request → GitHub main
```

Materialize only the manifest allowlist, require all source entries and the exact file inventory,
then validate and review the full delta before committing. Follow `skills/agentsetup-sync`,
Direction A, for explicit curated `STAGED` bindings and the PR procedure. The optional publisher
`-DryRun` scan supplements those checks; its real mode resets and wipes a disposable mirror and
must not target the prepared release checkout. `-NoPush` still performs those destructive steps.
Ingest foreign remote changes into the sources before publishing; Direction C records that flow.

**Releases:** governance generations are tagged (`v4`, `v5`, …). `git describe --tags` in your
cache repo shows which generation you run; re-run `Bootstrap.ps1` to upgrade.

Commit convention: `{type}({scope}): {summary}` — `feat` `fix` `docs` `chore` `refactor` `plan` `research`.

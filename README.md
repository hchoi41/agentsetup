# agentsetup

The published, version-controlled agent setup for my machines. One place that always holds the
latest install payload; a Windows computer can install it into a local hub.

> **This public repo excludes personal workspace data.** It is assembled from an explicit
> allowlist (`payload_manifest.json`). Before publication, the complete prepared payload must
> pass source/inventory verification, relevant tests, and a secret/PII/path scan. Working notes,
> memory, transcripts, and career material are excluded from the payload.

---

## Install on a Windows computer

**Begin in the AI conversation you already use:** copy the prompt in
[START-HERE.md](START-HERE.md). It asks which agent CLIs you want—choose one,
several, or **none**—before any CLI installation. No additional agent is needed
to ask this question. If the assistant cannot run commands locally, it provides
the commands for you to run.

The choices are Codex, Claude Code, GitHub Copilot CLI, Gemini CLI, and OpenCode.
None installs no CLI and disables Max's multi-agent runner while leaving your
current single-agent workflow available. OpenCode is currently solo-only in the
bundled system; Gemini's conductor adapter is experimental. A selected CLI does
not automatically supply every role required by a job.

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

### New computer: one command from the extracted ZIP

No Max-system folders need to exist. The clean installer creates the local hub and all missing
parent folders; it does not migrate or copy a personal workspace from another PC.

1. Download the ZIP from the [latest release](https://github.com/hchoi41/agentsetup/releases/latest)
   and choose **Extract All** into a local folder.
2. Open **PowerShell 7** in the extracted folder containing `Install-MaxAgentSystem.ps1`
   and `Get-AgentSetup.ps1`.
3. Run:

   ```powershell
   pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1
   ```

The destination defaults to `%LOCALAPPDATA%\AgentSetup\hub`. The wrapper uses the bundled
payload automatically and copies native skill adapters by default, so this ZIP route needs no
Git or existing setup. A missing or completely empty destination is accepted; any existing
contents, including hidden files, are refused. Use `Get-AgentSetup.ps1` below for an existing hub.

Optional examples, run from the same extracted folder:

```powershell
# Preview without creating the hub or its parent folders.
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1 -DryRun

# Choose a different local destination; missing parent folders are created.
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1 -Destination 'D:\AgentSetup\hub'

# Opt in to native skill junctions instead of the default copies.
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1 -UseJunctions
```

Choose one install command for your destination. After a successful install, open that hub as
your agent's working folder and fill in the identity placeholders at the top of `AGENTS.md`.
`01.PROJECT` starts empty, the project registry has no entries, and no personal history is imported.
See [INSTALL.md](INSTALL.md) for the folder tree, prerequisites, and first-use steps.

### Choose and install optional agent CLIs

Give your current assistant `00.ABOUT/AGENT_CLI_ONBOARDING.md` from the installed
hub, or use the release's [START-HERE.md](START-HERE.md). The `setup-agents` skill
uses `scripts/Setup-AgentCLIs.ps1` to inspect tools and show an install plan before
applying your selection. It installs only chosen missing CLIs and the approved
missing prerequisites, retaining existing tools. CLI dependency installation is
separate from hub folder creation and may require Windows administrator approval.

New hubs start with a pending CLI choice. Pending and none block multi-agent runs;
selected mode allows only configured providers the user selected. Existing hubs
without a choice file retain legacy behavior until opting in, and saved choices
survive upgrades. Selection is local and separate from the generated skill adapters.
Authentication is not inferred from installation: sign in through each tool's own
interface, then check the intended job's provider/model requirements. No paid AI
run is performed as an installation test.

### Network install or update

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
pwsh -NoProfile -File ./tests/install/Test-CleanInstall.ps1 -FixtureRoot "$env:TEMP/agentsetup-clean-install-review"
pwsh -NoProfile -File ./tests/install/Test-AgentCLIs.ps1 -FixtureRoot "$env:TEMP/agentsetup-cli-choice-review"
pwsh -NoProfile -File ./tests/install/Test-AgentSelection.ps1 -FixtureRoot "$env:TEMP/agentsetup-cli-guard-review"
```

The test leaves its disposable installation, logs, and `validation.json` for inspection.
It checks the new layout and skills, repeat installs, customization preservation, forced-change
backups, refusal cases, and unchanged user environment. It does not test your actual company
computer, configure accounts, or publish anything.
The clean-install test also covers absent parent folders, an isolated default location,
empty-folder installation, hidden-file refusal, and the extracted-source Bootstrap route.
The CLI setup suite simulates package managers in isolated child processes; it never
installs real provider software. The selection suite checks both actual engine
entrypoints with fake tools, proving disabled choices stop before provider calls.

---

## Layout

| Path | What |
|---|---|
| `Install-MaxAgentSystem.ps1` / `INSTALL.md` | Clean install from an extracted ZIP; first-use guide |
| `Bootstrap.ps1` / `Get-AgentSetup.ps1` | Network install/update and existing-hub upgrades |
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

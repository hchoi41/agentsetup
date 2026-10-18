# Install Max Agent System on a new Windows computer

This guide starts with no Max-system folders on the computer. The extracted release contains
the setup files. One command creates a new local hub, its parent folders, governance files,
empty project controls, skills, and agent adapters. No files from a personal PC are required.

## Before you start

Start with the AI assistant you already use: paste the prompt from
[START-HERE.md](START-HERE.md). It offers **one, several, or none** of Codex,
Claude Code, GitHub Copilot CLI, Gemini CLI, and OpenCode. Your current chat app
does not need its own CLI to guide this setup. No tool is selected automatically.

- **Windows and PowerShell 7+** are required. `pwsh --version` shows the installed version.
- Choose a local writable location outside OneDrive and separate from the extracted release.
  The default is `%LOCALAPPDATA%\AgentSetup\hub`.
- For this ZIP route, Git, Python, Node, AI apps, and AI accounts are not installation prerequisites.
  Install or sign in to the tools your workflows need separately. Git is required only for the
  network install/update route in the README.

On a managed computer, use its approved software channel to obtain PowerShell 7 and any
optional tools. The core hub installer does not install software, request administrator elevation,
change execution policy, or bypass company permissions. If policy blocks a script, use the
approved support route for that computer.

## Download, extract, run

1. Download the release ZIP from the [latest release](https://github.com/hchoi41/agentsetup/releases/latest).
2. In File Explorer, choose **Extract All**. Open the extracted folder that contains
   `Install-MaxAgentSystem.ps1` and `Get-AgentSetup.ps1`; do not run from inside the ZIP.
3. Open a terminal in that folder and select **PowerShell 7**. Run this one command:

   ```powershell
   pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1
   ```

You do not need to create `AgentSetup`, `hub`, or any Max-system folder first. The wrapper
finds its bundled source automatically, creates all missing destination parents, and installs
at `%LOCALAPPDATA%\AgentSetup\hub`. The extracted release and installed hub stay separate.
This local installation uses the extracted files without fetching from GitHub.

The destination may be absent or completely empty. A file at that path or any existing
contents—including hidden or system items—cause refusal. The clean installer does not
reuse an occupied hub or remove its contents. After your first successful install, use the
existing-hub upgrade route in the README rather than rerunning this clean-install command.

If installation stops after creating part of the hub, keep that folder and inspect the error.
After resolving its cause, resume from the extracted release with the existing installer:

```powershell
pwsh -NoProfile -File .\Get-AgentSetup.ps1 -SourceRoot . -Destination "$env:LOCALAPPDATA\AgentSetup\hub" -CopyAdapters
```

Use your chosen destination if you changed it. The clean wrapper intentionally refuses a
partially populated folder; the existing installer preserves local changes during a retry.

## Optional switches

Run these from the same extracted release folder. Choose the options you need for a single
installation; the examples are alternatives.

| Option | Effect |
| --- | --- |
| `-Destination '<local path>'` | Select a different hub; create missing parents automatically. |
| `-DryRun` | Validate and show the plan without creating the hub or its parent folders. |
| `-UseJunctions` | Use native skill directory junctions; the default uses copies. |

```powershell
# Preview the default location.
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1 -DryRun

# Install at another local location, including missing parent folders.
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1 -Destination 'D:\AgentSetup\hub'

# Opt in to junctions when suitable for your computer.
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1 -UseJunctions
```

Copied adapters are the default. Copilot prompt files and self-contained adapter documents
are generated in either mode. The installer verifies that all five core skills resolve through
the Claude Code, Codex, and OpenCode adapter folders before reporting installation complete.
It does not change user environment variables or install account-wide skills.

## What the new hub contains

```text
%LOCALAPPDATA%\AgentSetup\hub\
├── AGENTS.md                         Identity template and working rules
├── CLAUDE.md                         Startup pointers
├── 00.ABOUT\                         Governance and empty project controls
│   ├── must_read\                    Empty startup-context folder
│   ├── PROJECT_INDEX.md              Header only
│   └── PROJECT_REGISTRY.json         {"version":1,"projects":[]}
├── 01.PROJECT\                       Empty; your projects go here
├── 02.INPUT_TEMPLATES\
├── 10.OPERATION\
│   └── OPERATIONS_DASHBOARD.md        No running operations configured
├── 12.MEMORY_RAM\                    No personal working memory imported
├── 13.MEMORY_HDD\ingest\              Empty ingestion manifest
├── 14.LESSONS_LEARNED\
├── 15.WIKI\                          Starter log and empty wiki-template folder
├── workspaces\                       Empty local working area
├── skills\                           Canonical workflow procedures
├── scripts\                          Local tools and adapter generator
├── orchestrate\                      Conductor and configuration templates
├── tests\                            Bundled verification tools
├── mods\                             Opt-in Claude Code mods (not enabled by install)
├── .claude\skills\                   Claude Code skill copies by default
├── .agents\skills\                   Codex skill copies by default
├── .opencode\skills\                 OpenCode skill copies by default
├── .github\                          Generated Copilot instructions and prompts
└── microsoft_copilot\                Generated self-contained documents
```

The installer also records a local install ledger for future upgrades. No example projects,
personal project records, transcripts, career material, or private history are imported.
The five core workflows are `start-project`, `project-complete`, `cloud-sync`, `operationalize`,
and `projectify`. Installing their procedures does not run them, schedule jobs, or sign in to
an AI service.

## Begin using the hub

1. Open the installed hub as the working folder in your chosen agent application. At the
   default location, paste `%LOCALAPPDATA%\AgentSetup\hub` into File Explorer's address bar
   to find it, then choose that folder in the application.
2. Have the agent read `AGENTS.md`, `00.ABOUT/PROJECT_INDEX.md`, and
   `00.ABOUT/PROJECT_GOVERNANCE.md`. Fill the top of `AGENTS.md` with your hub/project name,
   one-line purpose, and status in place of its identity placeholders.
3. Give that agent `00.ABOUT/AGENT_CLI_ONBOARDING.md` and finish the optional CLI
   choice. The `setup-agents` skill previews missing tools and prerequisites, then
   installs only the approved choices. Choose none to install no CLI and keep
   multi-agent runs disabled. Required package installations may request Windows
   administrator approval; they do not bypass local policy. Sign-in stays in the
   providers' own interfaces, and installation alone does not prove authentication.
4. Describe your first project and use `start-project` to agree its plan and acceptance
   criteria before scaffolding it. The empty registry is expected; the installer does not
   invent a project for you.

A new project uses `01.PROJECT/<project>/10.INPUT`, `20.PROCESS`, and `30.OUTPUT`.
Project INPUT allows create/edit; deleting input files or folders requires the owner's explicit
permission. Operation INPUT remains read-only. Consult the installed governance for the full
access rules.

The Microsoft Copilot documents are for manual attachment or supported grounding; installing
them does not connect an account. Customize canonical `skills/` and `AGENTS.md` sources,
not generated adapter documents or copied skill surfaces, which are regenerated on an update.

A new hub begins with `00.ABOUT/AGENT_CLI_SETUP.json` set to pending. Pending and
none block the bundled multi-agent engines; choosing providers restricts jobs to
those providers. A single CLI is a valid choice but may not satisfy the default
Codex/Copilot/Claude job. OpenCode currently has no conductor adapter, and Gemini's
adapter is experimental. The setup helper preserves prior choices when changed,
and normal setup upgrades never replace an existing selection.

## Existing hubs and updates

Use [README.md](README.md), **Upgrades and local customization**, for an existing hub.
`Get-AgentSetup.ps1` remains the installer for existing project-first hubs, including preservation
of local source changes and the existing explicit replacement/backups flow. `Bootstrap.ps1`
provides the Git-backed network install/update route.

The clean wrapper is not a migration tool. A workspace with legacy role folders requires its
governed migration; never copy personal-PC folders into a new hub to satisfy installation.

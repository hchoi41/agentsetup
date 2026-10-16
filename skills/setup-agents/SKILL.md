---
name: setup-agents
description: Set up optional agent CLIs for Max Agent System. Use for first installation, missing agent commands, choosing one or several agents, or opting out of multi-agent execution. Begin with the current assistant asking the user to choose; never infer a selection.
---

# Agent and CLI onboarding

Start in the AI conversation the user is already using. Read the installed
`00.ABOUT/AGENT_CLI_ONBOARDING.md` or the release's `START-HERE.md`. CLI installation
is optional; the current chat app does not prove that its CLI exists.

1. If a hub exists, inspect without mutation using
   `scripts/Setup-AgentCLIs.ps1 -HubRoot <hub>`. Before a hub exists, inspect command
   availability with `Get-Command codex,claude,copilot,gemini,opencode` instead;
   missing commands are expected and are not a reason to skip the choice.
   Show the detected tools. Ask the user to choose **one, several, or none** from
   Codex, Claude Code, GitHub Copilot CLI, Gemini CLI, and OpenCode. No default
   selection. None is exclusive, keeps single-agent work available, and disables
   the bundled multi-agent runner. OpenCode is currently solo-only in this system;
   Gemini's existing conductor adapter is experimental. Microsoft 365 Copilot is
   not GitHub Copilot CLI and has no CLI installation route in this helper.
2. If this is an extracted release with no hub, explain the destination and use
   `Install-MaxAgentSystem.ps1` to create it. Do not assume the release folder is
   the runtime hub. A fresh hub's CLI choice begins pending.
3. Use `-Choose '<comma-separated IDs>'` without Apply to produce the concrete
   plan. IDs are `codex`, `claude`, `copilot`, `gemini`, `opencode`, or `none` alone.
   Show its prerequisites, commands, existing tools that will be kept, and blockers.
   Explicit approval of this plan authorizes its selected missing software and
   dependencies. If the user's existing instruction already approves the exact
   displayed scope, do not ask redundantly. No answer is never consent.
4. Run the same helper with `-Apply` only for the user's choice and authorized plan.
   For none, record it directly with `-Choose none -Apply`; no installer should run.
   Never silently upgrade existing CLIs, install unselected providers, change
   execution policy, use arbitrary package names, or work around company controls.
   If execution access is absent, give the exact commands for the user to run and
   wait for evidence; do not claim execution.
5. Check the result and explain any partial failure. Guide the user through official
   interactive sign-in separately. Never request passwords/API keys in chat, read
   credential stores, or launch a model prompt as a readiness probe. Report
   **selected**, **installed**, and **authenticated** separately; authentication is
   not checked by the setup helper. Missing facts remain unknown.
6. Keep the machine-local selection record. A single CLI is a valid choice, but a
   conductor job may require other CLIs. Its configured roles must use selected
   providers that the engine supports; do not automatically rewrite roles/models.
   The default job uses Codex + GitHub Copilot CLI + Claude Code. Claude-only and
   OpenCode-only selections cannot run that job. Run previews only when permitted
   by the selection guard, and obtain ordinary job authorization before any paid
   or file-changing multi-agent execution.

This procedure authorizes the setup helper to maintain only its named local
selection record and append-only prior-state backups after an explicit choice.
Other `00.ABOUT` governance files retain their normal access rules.

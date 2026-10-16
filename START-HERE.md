# Start with the AI agent you already use

Paste the following prompt into your current AI assistant. If it has access to this
extracted release or your installed hub, let it read this file directly. No additional
agent or CLI needs to be installed before this conversation.

> Help me set up Max Agent System on this Windows computer. Begin by asking which
> agents and CLIs I want: **Codex, Claude Code, GitHub Copilot CLI, Gemini CLI,
> OpenCode, or none**. Let me choose one or several; none is an exclusive choice.
> Do not preselect tools or treat my current chat application as an installed CLI.
> Explain that none keeps my current single-agent workflow available but disables
> Max's multi-agent runner. OpenCode currently supports single-agent use in this
> system; Gemini's conductor adapter is experimental. The default multi-agent job
> uses Codex, GitHub Copilot CLI and Claude Code. A different selection needs a
> compatible job configuration; do not silently substitute providers or models.
>
> Inspect the existing installation without changing it. If I need a clean hub,
> use the bundled Install-MaxAgentSystem.ps1 after explaining its local destination.
> After I choose, use scripts/Setup-AgentCLIs.ps1 to show the exact installation
> plan, including missing prerequisites. Install only the chosen missing CLIs and
> the dependencies included in that plan when I authorize it. Keep existing tools.
> If I choose none, record that choice without installing anything. Never guess
> an answer, change execution policy, or bypass this computer's restrictions.
>
> Guide me through any required sign-in in each tool's own official interface.
> Do not ask me to paste passwords or tokens into chat. Verify installation,
> distinguish it from authentication, and report what is still needed. Do not
> start an AI job or spend model credits as an installation test. If you cannot
> execute commands on my computer, give me the exact commands and wait for their
> results instead of claiming to have installed anything.

## The first question

The assisting agent should show the detected CLI availability beside these choices,
without selecting them automatically:

- **Codex** — OpenAI's CLI; supported by the bundled conductor.
- **Claude Code** — Anthropic's CLI; supported for reviewer/referee roles.
- **GitHub Copilot CLI** — GitHub's terminal agent; supported by the conductor.
- **Gemini CLI** — Google's CLI; the existing conductor adapter is experimental.
- **OpenCode** — native skill adapters; no bundled conductor adapter yet.
- **None** — install no CLI; use the current AI alone; multi-agent runs disabled.

Selecting one CLI is allowed. It does not automatically satisfy a job that requires
other providers. Microsoft 365/Windows Copilot is separate from GitHub Copilot CLI;
the former has manual document adapters here, not an installer or conductor CLI.

## What the helper does

From an installed hub, the assisting agent can inspect status or preview a choice:

```powershell
pwsh -NoProfile -File .\scripts\Setup-AgentCLIs.ps1 -HubRoot .
pwsh -NoProfile -File .\scripts\Setup-AgentCLIs.ps1 -HubRoot . -Choose 'codex,claude,copilot'
```

These commands do not install software or save a selection. After the user approves
the displayed plan, add `-Apply` with the same choice. To record none:

```powershell
pwsh -NoProfile -File .\scripts\Setup-AgentCLIs.ps1 -HubRoot . -Choose none -Apply
```

The helper records machine-local choice in `00.ABOUT/AGENT_CLI_SETUP.json`, preserving
the previous version before a change. New hubs begin with a pending choice. Pending
and none both block the bundled multi-agent engines before a job starts. Existing
hubs without a selection file keep their previous behavior until they opt in; an
existing choice survives setup upgrades. Re-run the conversation to change a choice.

Selecting tools authorizes neither a paid subscription nor a model run. Provider
account access, local software policy and a compatible job configuration still
matter. The helper reports installation separately from authentication.

## Official installation and sign-in references

The helper uses the official npm packages below. Node 22+ is this helper's shared
requirement, not a claim that every provider has the same minimum. When missing,
its plan includes the official WinGet `OpenJS.NodeJS.LTS` dependency; the package
may request Windows administrator approval. Existing selected CLIs are retained.
Git is optional workflow support, not automatically installed by this helper.

| Choice | Official package | Human sign-in after installation | Reference |
| --- | --- | --- | --- |
| Codex | `@openai/codex` | `codex login` | [OpenAI installation](https://help.openai.com/en/articles/11096431), [CLI reference](https://developers.openai.com/codex/cli/reference) |
| Claude Code | `@anthropic-ai/claude-code` | `claude auth login` | [Anthropic setup](https://code.claude.com/docs/en/setup), [CLI reference](https://code.claude.com/docs/en/cli-reference) |
| GitHub Copilot CLI | `@github/copilot` | `copilot login` | [GitHub installation](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli), [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) |
| Gemini CLI | `@google/gemini-cli` | Start `gemini`, then choose a sign-in method | [Google installation](https://geminicli.com/docs/get-started/installation/), [authentication](https://geminicli.com/docs/get-started/authentication/) |
| OpenCode | `opencode-ai` | `opencode auth login` | [OpenCode installation](https://opencode.ai/docs/), [CLI reference](https://opencode.ai/docs/cli/) |

Gemini's current documented Windows requirement is Windows 11 24H2 or newer.
The helper's plan reports unsupported Windows versions before installation.
Provider availability, subscriptions and organizational permissions remain separate.
Version checks do not prove login. Only describe an account as authenticated when
the official tool or the user supplies that evidence; never inspect credential files.

References checked 2026-10-16. [WinGet commands](https://learn.microsoft.com/en-us/windows/package-manager/winget/install)
and [npm global folder behavior](https://docs.npmjs.com/cli/v11/configuring-npm/folders/)
explain dependency installation and the existing npm prefix. The helper does not
change npm's global configuration or force an unwritable prefix.

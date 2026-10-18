# Claude Code mods

Opt-in [Claude Code mods](https://code.claude.com/docs/en/plugins/mods/overview) for the Max
agent setup. The installer copies this folder into the hub but enables nothing: each mod is
turned on per user, from Claude Code, with the two commands below.

This folder is a local plugin marketplace named `max-mods`. Claude Code reads a folder
marketplace in place, so a hub upgrade followed by `/reload-plugins` picks up new mod versions.

Built and tested on Claude Code 2.1.302. Mods are early access and their API can change between releases.

## token-ledger

Records every main-loop turn and learns which model and effort do each kind of job for the
fewest tokens.

**What it records per turn** (kept in the mod's own Claude Code store: up to the newest 1,500 turns, fewer if they would exceed 3 MB):

| Field | Meaning |
|---|---|
| `type` | Task type: a leading tag on the prompt (`#debug`, or `#type:anything` for your own type), or a free keyword classifier (`debug`, `test`, `review`, `refactor`, `docs`, `research`, `build`, `ops`, `question`, `other`) |
| `model`, `effort` | What the turn's last model request used, as the engine accepted it |
| `usage` | Input, output, cache-read and cache-write tokens; subagent runs and tokens separately, per subagent model |
| `steps`, `durationMs`, `outcome` | Model requests, wall time, and how the turn ended (`answer`, `aborted`, `refusal`, `error`) |
| `tools`, `commands` | Counts per tool, and per shell command name (`git`, `npm`, `pwsh`, ...) |
| `issues` | Qualitative log: tool errors and denied calls (command names plus the error text, credential-like values masked), repeated failing commands, abnormal turn ends; subagent issues are marked `[subagent]` |
| `rating` | Your verdict, from `/token-ledger rate good\|bad` |

**How it optimizes.** At the first model request of a turn it knows your current model and
effort. For the turn's task type it groups the last 50 turns by model and effort, ignoring
aborted turns, and keeps the groups with at least `minSamples` runs and an 80% success rate
(answered and not rated bad). Each group's expected cost is its median cost divided by its
success rate, because failed runs are paid for again. It recommends the cheapest group only
when all of these hold:

- it saves at least `minSavingsPct` against your current setting's own recent runs;
- its 75th-percentile cost is below your setting's median, so the win is not noise;
- the saving still holds after a switch penalty when the model changes (re-caching the context costs about 1.15× the tokens the last turn read from cache).

Cost is `input + 5×output + 1.25×cache write + 0.1×cache read` (the API's price ratios),
each model's tokens multiplied by its weight from `modelWeights`, subagents included.

**Starting point.** Until a task type has `minSamples` finished runs on this machine,
token-ledger starts from, in order: lessons carried in by hand, then the learned settings
shipped with the setup (both described below), then the provider guide's setting
(`token-ledger/hooks/defaults.ts`, documented with sources in
[`token-ledger/guide/MODEL_GUIDE.md`](token-ledger/guide/MODEL_GUIDE.md)). A setting marked to
avoid is skipped in favour of the next one. The guide
also lists starting settings for ChatGPT, Gemini, Grok and Muse for the other agents. A scheduled
task re-checks the official pages on the first of each month and opens a pull request when
something changed. Open-weight models are added only on request (`includeOpenModels` in
`token-ledger/guide/config.json`).

**Modes** (`/config` → token-ledger):

- `record`: log only.
- `suggest` (default): log, and show the recommended model and effort in the status line, with a notification when it beats your current setting.
- `auto`: also switch the turn's model requests to the recommendation. Every 5th turn of a type keeps your own setting so it keeps being measured. A recommended setting that ends a turn in an error is not applied again in that session. Only named efforts (`low` to `max`) are applied.

### Enable

At the Claude Code prompt, with `<hub>` your hub path:

```text
/plugin marketplace add <hub>/mods
/plugin install token-ledger@max-mods
```

From a shell: `claude plugin marketplace add <hub>/mods`, then
`claude plugin install token-ledger@max-mods`.

### Use

```text
#debug fix the failing login test      ← optional tag sets the task type (removed before the model sees it)
#type:migration move users table      ← a custom type
/token-ledger                          ← report by task type: tokens, success, top tools/commands, issues
/token-ledger last                     ← full breakdown of the last turn
/token-ledger recommend [type]         ← cheapest reliable model/effort per type
/token-ledger rate good|bad            ← mark the last finished turn (runs at once, even mid-turn); bad runs count as failures
/token-ledger lessons [path]           ← any machine: a short, plain-language lessons note (optionally saved to a file)
/token-ledger lessons-in <file>        ← main machine: take in a lessons note carried from another machine
/token-ledger forget <type>|all        ← drop lessons carried in for a type (or all)
/token-ledger learned [path]           ← main machine: write learned settings to ship with the setup (default ~/.claude/learned-defaults.json)
/token-ledger export [path]            ← write all local records as JSONL (default ~/.claude/token-ledger.jsonl)
/token-ledger reset confirm            ← delete all records
```

Options: `mode`, `minSamples` (8), `minSavingsPct` (15), `modelWeights` (for example
`haiku=1,sonnet=3,opus=5`; empty compares tokens only, so set it to compare cost across
models), and `logPath` (a JSONL file rewritten after every turn; relative to Claude Code's working
folder, `~` not expanded). If saving fails, the status line says `ledger: not saved`.

### Main machine and other machines

Records never leave the machine that made them: no syncing, no network, nothing written
outside that machine. This keeps a company computer's data inside the company's network.

What travels is the setup, one way, from your main machine through GitHub:

1. On the main machine, `/token-ledger learned` writes `learned-defaults.json`: per task type,
   the cheapest reliable model and effort, with sample size and success rate, plus settings to
   avoid. It combines the settings already shipped, the lessons carried in, and the main
   machine's own evidence (newest wins). It holds no records, paths, commands or machine names.
2. Copy it to `mods/token-ledger/guide/learned-defaults.json` in the setup source (OneDrive
   `STAGED/mods/token-ledger/guide/`) and publish as usual.
3. Every machine that installs or updates the setup starts from those settings (see
   *Starting point*). After `minSamples` runs of a type its own evidence decides.

**Before the first publish with this mod:** put `mods/` (both `.claude-plugin/marketplace.json`
and `README.md`, and the `token-ledger/` folder) into OneDrive `STAGED/mods/`. The manifest lists
those sources; the publish script now stops with an error when a listed source is missing,
instead of publishing its deletion. Changes this mod made to `README.md`, `INSTALL.md`,
`Get-AgentSetup.ps1`, `Publish-AgentSetup.ps1`, `tests/install/Test-Install.ps1` and the two
`.gitignore` files must be copied into their STAGED or `AGENTS_SETUP` sources too, or the next
publish reverts them.

### Carrying lessons by hand

A machine that must not send data out (a company computer) can still teach the main machine,
through you. `/token-ledger lessons` prints a note like this:

```text
token-ledger lessons, 2026-10-18, 32 turns
1. debug: use claude-sonnet-5-5 / high, 44% cheaper than claude-opus-5-5 / medium (n=9, 100% success)
2. question: use claude-haiku-5-5 / low (n=10, 100% success)
Avoid: build on claude-sonnet-5-5 / low (2 of 4 failed)
Common problems: tool-error (Bash) ×4, retry (Bash) ×3
```

It holds only task types, model names, effort levels, counts and issue kinds (tool names
for built-in tools; MCP and plugin tools appear only as `mcp` or `other`, since their names can
carry server or company names). A custom `#type:` tag appears exactly as you typed it, so
choose tags you are happy to carry out. It holds no paths,
commands, prompts or error text. Read it, check it is fine to take out under your company's
rules, and carry it to the main machine (retyping the numbered lines is enough). There, save
it to a file and run `/token-ledger lessons-in <file>`. The numbered lines fill task types the
main machine has no evidence for, and go out to every machine with the next
`/token-ledger learned` and publish.

### Limits

- The task type comes from keywords unless you tag the prompt. Tag the prompts you care about.
- "Success" means the turn ended with an answer you did not rate bad. A wrong answer you do not rate counts as a success, so rate the turns that went wrong.
- The evidence is observational: if you pick a small model only for easy jobs, it will look cheap. Tagging by type limits this but does not remove it.
- Switching models (`auto`) starts a new prompt cache. The switch penalty accounts for this when deciding, but the switched-to setting's own records still carry that re-caching cost.
- Subagent work is counted against the turn whose Agent call started it, even when it finishes later. Loops no Agent call started (Claude Code's own internal calls, teammates started another way) are not counted.
- A turn in progress while the mod reloads is not recorded.
- Local records (the mod's store, `logPath`, `export`) include the working-folder path, command names and clipped error text. Keep `logPath` and `export` files out of public repositories and out of synced folders (OneDrive and the like) on a machine whose data must stay inside; both default to `~/.claude`. New hubs' `.gitignore` ignores `token-ledger*.jsonl`; an existing hub keeps its own `.gitignore`.
- A setting is marked to avoid only after 5+ runs of a type with 2+ failures and under 80% success. A newer lessons note replaces the avoid list for the types it mentions; `forget` drops carried-in lessons.

### Verify

```text
claude plugin validate <hub>/mods/token-ledger
claude plugin test <hub>/mods/token-ledger
```

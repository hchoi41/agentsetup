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

**Starting point.** Until a task type has `minSamples` finished runs, token-ledger starts from
the provider guide's setting for that type (`token-ledger/hooks/defaults.ts`, documented with
sources in [`token-ledger/guide/MODEL_GUIDE.md`](token-ledger/guide/MODEL_GUIDE.md)). The guide
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
   the cheapest reliable model and effort its own evidence supports, with sample size and
   success rate. It holds no records, paths, commands or machine names.
2. Copy it to `mods/token-ledger/guide/learned-defaults.json` in the setup source (OneDrive
   `STAGED/mods/token-ledger/guide/`) and publish as usual.
3. Every machine that installs or updates the setup starts from those settings. Until a
   machine has `minSamples` runs of a type it uses, in order: the main machine's learned
   setting, then the provider guide. After that its own evidence decides.

### Limits

- The task type comes from keywords unless you tag the prompt. Tag the prompts you care about.
- "Success" means the turn ended with an answer you did not rate bad. A wrong answer you do not rate counts as a success, so rate the turns that went wrong.
- The evidence is observational: if you pick a small model only for easy jobs, it will look cheap. Tagging by type limits this but does not remove it.
- Switching models (`auto`) starts a new prompt cache. The switch penalty accounts for this when deciding, but the switched-to setting's own records still carry that re-caching cost.
- Subagent work is counted against the turn whose Agent call started it, even when it finishes later. Loops no Agent call started (Claude Code's own internal calls, teammates started another way) are not counted.
- A turn in progress while the mod reloads is not recorded.
- Local records (the mod's store, `logPath`, `export`) include the working-folder path, command names and clipped error text; keep those files out of public repositories (the hub's `.gitignore` ignores `token-ledger*.jsonl`).

### Verify

```text
claude plugin validate <hub>/mods/token-ledger
claude plugin test <hub>/mods/token-ledger
```

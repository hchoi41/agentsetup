# Claude Code mods

Opt-in [Claude Code mods](https://code.claude.com/docs/en/plugins/mods/overview) for the Max
agent setup. The installer copies this folder into the hub but enables nothing: each mod is
turned on per user, from Claude Code, with the two commands below.

This folder is a local plugin marketplace named `max-mods`. Claude Code reads a folder
marketplace in place, so a hub upgrade followed by `/reload-plugins` picks up new mod versions.

Requires Claude Code 2.1.287 or later (mods are early access; built and tested on 2.1.302).

## token-ledger

Records every main-loop turn and learns which model and effort do each kind of job for the
fewest tokens.

**What it records per turn** (kept in the mod's own Claude Code store, newest 1,500 turns):

| Field | Meaning |
|---|---|
| `type` | Task type: a leading `#tag` on the prompt, or a free keyword classifier (`debug`, `test`, `review`, `refactor`, `docs`, `research`, `build`, `ops`, `question`, `other`) |
| `model`, `effort` | What the turn's model requests actually used |
| `usage` | Input, output, cache-read and cache-write tokens; subagent runs and tokens separately |
| `steps`, `durationMs`, `outcome` | Model requests, wall time, and how the turn ended (`answer`, `aborted`, `refusal`, `error`) |
| `tools`, `commands` | Counts per tool, and per shell command name (`git`, `npm`, `pwsh`, ...) |
| `issues` | Qualitative log: tool errors (with a clipped message), denied calls, repeated failing commands, abnormal turn ends |
| `rating` | Your verdict, from `/token-ledger rate good\|bad` |

**How it optimizes.** For a task type, it groups past turns by model and effort and keeps the
groups with at least `minSamples` runs and an 80% success rate (answered and not rated bad).
It recommends the group with the lowest median cost, if that saves at least `minSavingsPct`
on the setting you are using now. Cost is
`input + 5×output + 1.25×cache write + 0.1×cache read` (the API's price ratios), multiplied
by an optional per-model weight.

**Modes** (`/config` → token-ledger):

- `record`: log only.
- `suggest` (default): log, and show the recommended model and effort in the status line, with a notification when it beats your current setting.
- `auto`: also switch the turn's model requests to the recommendation.

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
/token-ledger                          ← report by task type: tokens, success, top tools/commands, issues
/token-ledger last                     ← full breakdown of the last turn
/token-ledger recommend [type]         ← cheapest reliable model/effort per type
/token-ledger rate good|bad            ← mark the last turn's outcome (bad runs never count as cheap wins)
/token-ledger export [path]            ← write all records as JSONL (default ./token-ledger.jsonl)
/token-ledger reset confirm            ← delete all records
```

Options: `mode`, `minSamples` (3), `minSavingsPct` (15), `modelWeights` (for example
`haiku=1,sonnet=3,opus=5`; empty compares tokens only, so set it to compare cost across
models), and `logPath` (a JSONL file rewritten after every turn).

### Limits

- The task type comes from keywords unless you tag the prompt. Tag the prompts you care about.
- Switching model mid-conversation (`auto`) starts a new prompt cache. The extra cache writes are counted in later turns, so the ledger learns this cost, but expect the first switched turns to look expensive.
- Subagent tokens are counted against the main turn they ran under. A background agent that finishes after its turn is not counted.
- Records stay on the machine (the mod's store, plus `logPath` or `export` files if you choose). Nothing is sent anywhere.

### Verify

```text
claude plugin validate <hub>/mods/token-ledger
claude plugin test <hub>/mods/token-ledger
```

# tikitaka

tikitaka is the build-and-review loop engine in `orchestrate/tikitaka`. You give it a job config (a JSON file) that names a spec, the sources the artifact must be accurate to, and the roles that build, review, judge, check and audit. The engine runs the loop. Gates, access control and the records are enforced in code. It then writes everything it did to a run directory.

This README is for the user and for any AI agent setting up a job.

## Authority and tests

- **The role charter is the authority** (`docs/tikitaka_role_charter.md` in the agentsetup package). It is the source of truth for roles, gates, access and the loop. If this README and the charter disagree, the charter wins. The code cites the charter by section:
  - `config.py`: job config, charter §2
  - `access.py`: overrides and double-check, charter §3.6 and §5
  - `engine.py`: the loop, charter §4, §6 and §7
  - `runlog.py`: the run directory, charter §8
- **The test suite is in `tests/tikitaka`.** The `fake` CLI (see the roles block) lets tests run seats as local Python scripts.

## Running a job

Run these commands from `orchestrate/`:

```
python -m tikitaka run --config job.json [--config-from-user] [--project SLUG] [--session ID]
                       [--override role=ENV1,ENV2] [--hub PATH] [--run-dir PATH]
python -m tikitaka profiles [--no-status]
python -m tikitaka profiles create --cli claude|codex --name NAME
python -m tikitaka profiles login  --cli claude|codex --name NAME
python -m tikitaka clear-project-overrides SLUG
```

### `run` flags

| Flag | Meaning |
|---|---|
| `--config PATH` | Required. The job config JSON. Relative paths inside it (`spec`, `sources`) resolve against the config file's own directory. |
| `--config-from-user` | Pass this only when the user wrote or passed the config. Without it, the config is treated as agent-written and its `double_check` block is ignored (see [Double-check](#double-check)). |
| `--project SLUG` | Sets the project slug and replaces any `project` value in the config. This selects the project settings file. |
| `--session ID` | Sets the session id. This selects the session settings file when no project is set. |
| `--override role=ENV1,ENV2` | Lets the named role's worker receive the listed environment variables, which are otherwise scrubbed. Repeat the flag to add more overrides. Only the user may type this flag (see [Live-access rule](#live-access-rule-for-workers)). |
| `--hub PATH` | The hub root. The default is the directory that contains `orchestrate/`. |
| `--run-dir PATH` | Where the run directory goes. The default is `<hub>/_orchestrate/<job name>_<YYYYmmdd_HHMMSS>`. |

When the run ends, the command prints `<status>: <run directory>` and exits with the code given in [Statuses and exit codes](#statuses-and-exit-codes).

Two runners can never write to one run directory at the same time. A run holds `run.lock` in its run directory and deletes it at the end. If the lock already exists, the command stops with a `PreflightError` "another runner holds …"; Python prints a traceback and exits 1. The engine does not detect stale locks. If a run crashed or was killed, `run.lock` remains; check that the pid it holds is no longer running, then delete the file by hand.

If the job config is invalid, `run` stops with a `ConfigError` before any run starts; Python prints a traceback and exits 1. Examples: a mandatory role is missing, `spec` is missing, or two roles share a seat alias.

### Agent profiles

The engine uses the agent profiles already set up on the machine. It ships with no profile names. A profile is a CLI's own config folder in the user's home directory:

| CLI | Profile folders | Profile name | Given to the seat as |
|---|---|---|---|
| `claude` | `~/.claude`, `~/.claude-<x>` | `claude`, `claude-<x>` | `CLAUDE_CONFIG_DIR` |
| `codex` | `~/.codex`, `~/.codex-<x>` | `codex`, `codex-<x>` | `CODEX_HOME` |

A folder counts only when it holds the CLI's own files (`.claude.json`, `.credentials.json`, `settings.json` or `projects/` for claude; `auth.json` or `config.toml` for codex). A folder set in `CLAUDE_CONFIG_DIR` or `CODEX_HOME` is found too. `python -m tikitaka profiles` lists the profiles with each one's login status, read from the CLI's own status command (`claude auth status`, `codex login status`).

Every `claude` and `codex` seat names its profile in the job config (`"profile": "claude-x"`). Before any model is called, `run` checks that each named profile exists and is logged in. If not:

- **In a terminal**, it shows what is missing and the profiles that exist, then asks whether to create the profile or log in to it. If none are found at all, it asks "No agent profiles found. Create agent profiles now?". Creating a profile makes the folder and runs the CLI's own login (`claude auth login`, `codex login`). The engine never reads or stores credentials. Answering no stops the run and changes nothing.
- **Without a terminal** (an agent running the job), it prints the exact `profiles create` or `profiles login` command, makes no change, and exits 3, so the agent must ask the user.

`profiles create --name` takes `<cli>` (the default folder) or `<cli>-<name>` with lowercase letters, digits, `-` or `_`. It refuses a folder that is already a profile.

### `clear-project-overrides SLUG`

Run this when a project is finished to drop its overrides and double-check setting; the hub's project-complete procedure calls it. It edits the project settings file for `SLUG`:

1. It removes `overrides`.
2. It removes `double_check`.
3. It sets `"completed": true`.

It prints `cleared overrides for project SLUG` and exits 0.

## What a run does

1. **Preflight.** The engine:
   - collects active overrides;
   - rejects overrides found in agent-writable places;
   - checks that every `claude` and `codex` CLI the roles use resolves to a real application file, not a shell function or alias;
   - checks that every `claude` and `codex` seat's profile exists on this machine and is logged in (see [Agent profiles](#agent-profiles));
   - runs the `require_logins` checks.
2. **Research (optional).** If a `researcher` role exists, it runs on the brief. Then any `before_plan` extension roles run.
3. **Plan.** The engine writes `plan.md` from the brief, planned functions, milestones and roles. The judge must agree before the run continues. This is the `plan_approval` gate.
4. **Milestone loops.** Each milestone runs one loop. With no milestones, there is one loop, `m1`. Each round:
   1. `build` extensions run.
   2. The builder writes a new artifact version.
   3. The reviewers run in parallel. When double-check is active, a second pair runs too.
   4. `review` extensions run.
   5. The judge gives a verdict.

   The loop ends when the judge says `improvement_necessary: "no"`, when `max_iterations` is reached, or when `token_cap` is reached. `stopped_by_cap` (seen in `audit_digest.json`) is set both when the cap stops a loop and when `max_iterations` is reached without the judge saying `improvement_necessary: "no"`. The judge must then agree that the milestone is reached. This is the `milestone_m<k>` gate.
5. **QA/QC.** QA/QC checks each planned function against the final artifact. If any function fails and the judge agrees, the engine makes one bounded fix. This is the `qa_failure_handling` gate. The bounded fix:
   1. The builder changes only what the failed functions need.
   2. The accuracy reviewer reviews the fix.
   3. The judge decides whether to accept the fix.
   4. QA/QC runs again.

   A second failure escalates. There is never a second bounded fix.
6. **Finish.** The judge must agree that the run should finish with the final version. This is the `finish` gate.
7. **Audit.** The engine always writes `audit_digest.json` and `AUDIT.md`, even when the run escalates or fails preflight.

Reviews, verdicts, agreements and QA/QC results are validated by code. An invalid output, or a cut-off review, verdict or agreement, is rejected and the seat is called again, up to 3 attempts per seat; a cut-off retry uses one of those attempts. For `accuracy_reviewer` and `cold_reviewer` (and the `_2` pair), if all 3 primary attempts fail and the role has a `replacement`, the replacement seat gets its own 3 attempts. The judge's acceptance of a QA bounded fix gets one attempt; an invalid answer escalates.

## Job config

### Example

```json
{
  "name": "tikitaka_readme",
  "spec": "spec.md",
  "sources": ["../tikitaka/config.py", "../tikitaka/engine.py"],
  "brief": "Write orchestrate/tikitaka/README.md per the spec.",
  "artifact_name": "README.md",
  "planned_functions": ["Explains how to run a job", "Lists the run directory files"],
  "milestones": [],
  "max_iterations": 2,
  "token_cap": null,
  "project": "my-project",
  "user_channel": true,
  "require_logins": ["cloud"],
  "login_commands": {"cloud": ["gcloud", "auth", "print-access-token", "--quiet"]},
  "roles": {
    "orchestrator":      {"seat": "engine",     "cli": "internal"},
    "builder":           {"seat": "bld",        "cli": "claude", "profile": "claude",   "model": "claude-opus-5-5", "effort": "high"},
    "accuracy_reviewer": {"seat": "rev1",       "cli": "codex",  "profile": "codex",    "model": "gpt-6-astra", "effort": "ultra", "sandbox": "read-only",
                          "replacement": {"seat": "bld.acc", "cli": "claude", "profile": "claude", "model": "claude-opus-5-5", "effort": "high"}},
    "cold_reviewer":     {"seat": "rev2",       "cli": "claude", "profile": "claude-b", "model": "claude-fable-5-1", "effort": "high"},
    "judge":             {"seat": "rev2.judge", "cli": "claude", "profile": "claude-b", "model": "claude-fable-5-1", "effort": "high"},
    "qa_qc":             {"seat": "rev1.qa",    "cli": "codex",  "profile": "codex",    "model": "gpt-6-astra", "effort": "ultra"},
    "auditor":           {"seat": "rev2.audit", "cli": "claude", "profile": "claude-b", "model": "claude-fable-5-1", "effort": "medium"},
    "style_checker":     {"seat": "sty",        "cli": "claude", "profile": "claude",   "model": "claude-sonnet-5-5", "stage": "build",
                          "receives": ["plan"], "hands_off_to": "builder",
                          "instruction": "List style rules the builder must follow."}
  }
}
```

Seat aliases (`bld`, `rev1`, ...) and profile names (`claude`, `claude-b`, `codex`) are examples: use the profiles `python -m tikitaka profiles` lists on your machine. `require_logins` is only for jobs that need an outside login (here a cloud CLI); most jobs leave it out.

### Top-level keys

| Key | Default | Meaning |
|---|---|---|
| `name` | config file name without extension | The job name. It is used in the plan, the audit and the default run directory name. |
| `spec` | **required** | Path to the spec the artifact must satisfy. Every reviewer, the judge and the builder receive it. |
| `sources` | `[]` | Paths to files the artifact must be accurate to. The builder, accuracy reviewers and judge receive their contents. The cold reviewer does not, by design. |
| `brief` | `""` | Short task description. It goes into the plan and is the input to the researcher and to `before_plan` extensions. |
| `artifact_name` | `"artifact.md"` | File name for saved versions: `artifacts/v<N>_<artifact_name>`. |
| `planned_functions` | `[]` | The functions the artifact must deliver. QA/QC gives each one a pass or fail, and a pass needs a verbatim quote from the artifact as evidence. |
| `milestones` | `[]` | One loop runs per entry, keyed `m1`, `m2`, …. An empty list means one loop, `m1`. |
| `max_iterations` | `3` | Maximum rounds per milestone loop. Must be ≥ 1. |
| `token_cap` | `null` | Token threshold. It is checked only before each milestone round; if the running total has reached it, that loop stops. A round in progress, milestone agreements, QA/QC, the bounded fix, the finish gate and the audit still run and can push the total past the cap. `stopped_by_cap` (seen in `audit_digest.json`) is set both when the cap stops a loop and when `max_iterations` is reached without the judge saying `improvement_necessary: "no"`. `null` means no cap. |
| `double_check` | `{"enabled": false}` | Adds a second reviewer pair. It is honoured only with `--config-from-user`. See [Double-check](#double-check). |
| `project` | `null` | Project slug. It selects `projects/<slug>.json` in the settings root. `--project` replaces it. |
| `user_channel` | `true` | Boolean. It is loaded into the job. The loop in this version does not act on it. |
| `require_logins` | `[]` | Login names checked during preflight, for jobs that need an outside login (a cloud CLI, a workspace account). A failed login ends the run as `preflight_failed`. |
| `login_commands` | `{}` | Map of login name to a command (argv list) that must exit 0 when the login is valid. There are no built-in logins: every name in `require_logins` needs a command here, or the config is rejected. |
| `roles` | **required** | See [Roles block](#roles-block). |

Do not put a top-level `overrides` key in the job config. The job config is an agent-writable source. Overrides found there are rejected, never applied, and logged in `overrides.json` and `process.md`.

### Roles block

**Mandatory roles:**

- `orchestrator`
- `builder`
- `accuracy_reviewer`
- `cold_reviewer`
- `judge`
- `qa_qc`
- `auditor`

**Optional built-in roles:**

- `researcher`
- `accuracy_reviewer_2` and `cold_reviewer_2`: the double-check pair.

**Extension roles.** Any other role name is an extension role, and it must set `stage`.

| Key | Meaning |
|---|---|
| `seat` | **Required.** The seat alias. It must be unique across all roles. It labels one role instance, so one agent can fill several roles under different aliases, for example `rev2` and `rev2.judge`. |
| `cli` | **Required.** One of `claude`, `codex`, `copilot`, `fake`, `internal`. Notes on some values: <ul><li>`internal` is accepted for any role by validation, but there is no `internal` seat adapter. Use it only for `orchestrator` (never dispatched) and `auditor` (the engine writes the fallback audit). Any other role set to `internal` fails when it is called.</li><li>`copilot` passes validation but has no seat adapter in this version, so a role that is actually called with it fails.</li><li>`fake` runs the Python script named by the role's `script` key, for tests.</li></ul> |
| `model` | The model name. It is passed to the CLI (`--model` for claude, `-m` for codex). |
| `effort` | Reasoning effort (`--effort` for claude, `model_reasoning_effort` for codex). Defaults to `high` at call time. |
| `profile` | **Required for `claude` and `codex` seats.** The name of an agent profile on this machine (`claude`, `claude-<x>`, `codex`, `codex-<x>`), or the path of one. The engine checks it exists and is logged in, then sets it for that seat only (`CLAUDE_CONFIG_DIR` or `CODEX_HOME`). See [Agent profiles](#agent-profiles). |
| `sandbox` | `codex` only. The sandbox mode, default `read-only`. These restrictions apply: <ul><li>Review seats must run `read-only`. The review seats are `accuracy_reviewer`, `cold_reviewer`, both `_2` reviewers, `judge`, `qa_qc` and `auditor`.</li><li>`workspace-write` is refused on Windows.</li><li>The sandbox check runs when the seat is called, not in the Preflight step. A violation raises a preflight error and the run ends `preflight_failed`, so this can happen mid-run after artifacts exist.</li><li>A sandbox violation on the auditor seat does not change the status; the engine logs the failure and writes the fallback audit.</li></ul> |
| `cutoff_seconds` | The only way to put a time limit on a seat. See the [cut-off prevention rule](#cut-off-prevention-rule). |
| `replacement` | A fallback seat for reviewer roles (`accuracy_reviewer`, `cold_reviewer` and their `_2` pair), written as an object with `seat`, `cli`, `model`, `effort` and any other role keys. It is used only when all 3 primary attempts fail (invalid or cut-off output). The replacement seat then gets its own 3 attempts. The swap is recorded in the audit. |
| `stage` | When an extension role runs. It must be one of `before_plan`, `build`, `review`, `verdict`, `after_loop`, `final`. <ul><li>Built-in roles have fixed stages, so they do not need this key.</li><li>This version dispatches extension roles only at `before_plan`, `build` and `review`.</li><li>A role with `verdict`, `after_loop` or `final` passes validation but is not called.</li></ul> |
| `receives` | A list of the inputs the role is meant to receive, following the charter's lists. The engine stores the list. The actual inputs are fixed by stage: <ul><li>`before_plan` gets the brief.</li><li>`build` gets the plan.</li><li>`review` gets the current artifact.</li></ul> |
| `hands_off_to` | The role the output is meant for. It is recorded in `extensions/<stage>_<role>.json` and labels the output. What happens to the output depends on the stage: <ul><li>`before_plan` output is added to the plan's research section.</li><li>`build` output is given to the builder as additional input.</li><li>`review` output is recorded only.</li></ul> |
| `instruction` | The task text in an extension role's prompt. |

Two more role keys exist:

- `setting_sources`: `claude` only. Passed to `--setting-sources`; empty by default.
- `script`: `fake` only.

## User overrides and double-check settings

### Where they live

User-owned settings live outside the hub and outside run directories.

The settings root is `$TIKITAKA_SETTINGS_ROOT`. If that variable is not set, it is `~/.maxagent/tikitaka`.

| File | Scope |
|---|---|
| `<settings root>/sessions/<session id>.json` | Session. Read only when the job has **no** project and `--session` is given. |
| `<settings root>/projects/<slug>.json` | Project. Read when the job has a project that is not completed. |

Each file may hold:

```json
{"overrides": [{"roles": ["builder"], "access": ["GH_TOKEN"], "reason": "push the branch"}],
 "double_check": {"enabled": true}}
```

Project and session files take only `{"enabled": true|false}` for `double_check`; `duration` and `start_round` are ignored there.

**Completed project.** A project is completed when its settings file has `"completed": true` (set by `clear-project-overrides`). For overrides only, the engine also treats a project as completed when the hub's `00.ABOUT/PROJECT_INDEX.md` lists its display name (from `00.ABOUT/PROJECT_REGISTRY.json`) as `completed`. The double-check check uses the settings file only, so an index-completed project still applies its project `double_check` setting.

### Override sources and lifetimes

Overrides are accepted from three sources only, all user-owned:

| Source | How long it lasts |
|---|---|
| `--override role=ENV1,ENV2` | This run only. It is recorded with the session id. |
| `projects/<slug>.json` `overrides` | Every run of that project until the project is completed (see [Completed project](#where-they-live)). |
| `sessions/<id>.json` `overrides` | Every run started with that `--session` id and no project, for as long as the file holds them. |

Agent-written sources are never read as override sources. Agent-written sources are the job config, the run directory and relayed prompts.

`overrides.json` in the run directory lists every override, accepted or rejected, with its scope and source.

### Double-check

Double-check adds `accuracy_reviewer_2` and `cold_reviewer_2` to each review round. During a QA bounded fix, it adds `accuracy_reviewer_2` to the fix review. If double-check is active but either role is missing, the run escalates.

The engine uses the first source in this list that applies:

1. **Project file** `double_check`, if the job has a project that is not completed by the settings-file test (see [Completed project](#where-they-live)). `{"enabled": true}` makes double-check active for every round of every run of that project, until `clear-project-overrides` removes it. `{"enabled": false}` turns it off even if the job config enables it.
2. **Session file** `double_check`, if the job has no project and `--session` is given. It applies to every round of runs with that session id, as long as the file holds it.
3. **Job config** `double_check`. It is honoured only with `--config-from-user`. Otherwise it is ignored, and `process.md` logs that it was ignored. Use this form:
   ```json
   "double_check": {"enabled": true, "duration": {"type": "iterations", "n": 2}, "start_round": 1}
   ```
   - `duration.type` accepts only `iterations`.
   - `duration.n` must be ≥ 1.
   - When `enabled` is true, `duration` is required in practice: validation accepts its absence, but the run then fails with an uncaught `KeyError` when the first review set is assembled (traceback, exit 1).
   - Double-check is active for run-wide rounds `start_round` through `start_round + n - 1`.
   - `start_round` defaults to 1.
   - Run-wide rounds count across all milestones.

The state used in each round is written to `double_check.json`.

## Run directory

| Path | Contents |
|---|---|
| `run.lock` | Present only while the run is running. It holds the runner's process id. The engine does not detect stale locks. If a run crashed or was killed, `run.lock` remains; check that the pid it holds is no longer running, then delete the file by hand. |
| `process.md` | The process record. The code appends a timestamped line for every event: calls, gate passes and rejections, cut-offs, overrides, stops and escalation. |
| `plan.md` | The plan. |
| `overrides.json` | Accepted overrides, with scope and source, and rejected overrides. |
| `double_check.json` | The double-check state for the latest round. It is overwritten each round. |
| `tokens.json` | Tokens per call: role, seat, milestone, round, tokens and usage. |
| `cutoffs.json` | Every cut-off, with its role, seat, trigger and reason. Written only if a cut-off happened. |
| `calls/` | Raw seat I/O. Each call's base name is `<tag>_<seat>`, and each file's extension is applied with `Path.with_suffix`, which replaces whatever follows the last dot in that base name. So for a seat alias that contains a dot, the part after its last dot is dropped: calls for seat `rev2.judge` produce files ending `_rev2.md`, `_rev2.prompt.md` and so on, not `_rev2.judge.md`. For each completed call: <ul><li>`.prompt.md`: the prompt sent;</li><li>`.md`: the output text;</li><li>`.stdout.txt`: raw standard output;</li><li>`.err`: standard error;</li><li>`.events.jsonl`: the event stream, codex only.</li></ul> If `cutoff_seconds` fires, only the `.prompt.md` file is guaranteed; the `.md`, `.stdout.txt`, `.err` and `.events.jsonl` files are not written (a codex seat may still have written its own `.md` output). |
| `artifacts/v<N>_<artifact_name>` | Each artifact version. |
| `artifacts/v<N>.changes.md` | The builder's change note for each version. |
| `reviews/<milestone>_r<round>_<role>_<seat>.json` | Gate-validated reviews. QA bounded-fix reviews are also saved as `reviews/qafix_<role>_<seat>.json`. |
| `verdicts/<milestone>_r<round>_judge.json` | Judge verdicts. |
| `verdicts/qafix_judge.json` | The judge's decision on the bounded fix. |
| `agreements/<key>_seq<N>.json` | Judge agreements for the gates `plan_approval`, `milestone_m<k>`, `qa_failure_handling` and `finish`. |
| `consultations/` | Judge consultations, if any, as `<n>.json`. |
| `extensions/<stage>_<role>.json` | Extension role outputs, with `hands_off_to`. |
| `qa.json` | The first QA/QC result. If a bounded fix is made and the rerun passes, this file is overwritten with the rerun result. If the rerun fails, it keeps the first result. |
| `qa_rerun.json` | The QA/QC result after a bounded fix, pass or fail. Present only when a bounded fix was made. |
| `ESCALATION.md` | Written only when the run escalates. It states the failure and the decision needed. |
| `audit_digest.json` | A machine digest of the run: status, final version, verdicts, rejected and unfixable items, replacements, cut-offs, tokens, process record and file list. |
| `AUDIT.md` | The human-readable audit. The auditor seat writes it. If that seat is `internal`, fails or returns nothing, the engine writes a fallback audit. |

## Statuses and exit codes

| Status | Exit code | Meaning |
|---|---|---|
| `finished` | 0 | All gates passed and QA/QC passed. The final version is the last `artifacts/v<N>_…` file. |
| `escalated` | 3 | The engine could not proceed without a human decision. See `ESCALATION.md`. Causes include: <ul><li>a gate with no valid judge agreement;</li><li>an incomplete review set;</li><li>no valid verdict;</li><li>the builder returned no complete artifact;</li><li>QA/QC failed after the bounded fix.</li></ul> |
| `preflight_failed` | 1 | A tool-setup check failed. See `process.md`. The CLI and login checks run in the Preflight step; the sandbox check and the 8000-character command-line check run when a seat is called, not in the Preflight step, so `preflight_failed` can occur mid-run after artifacts exist. Causes include: <ul><li>a CLI not found as an application;</li><li>an expired login;</li><li>a disallowed sandbox;</li><li>the seat command line (executable, flags, model, effort, `setting_sources` path and, for codex, the output file path) exceeds 8000 characters; prompts go on stdin and never count.</li></ul> |

Two failures happen before a run starts and are not statuses: an invalid job config raises `ConfigError`, and an existing `run.lock` raises `PreflightError` ("another runner holds …"). Both are uncaught, so Python prints a traceback and exits 1, and no run directory records are written. `clear-project-overrides` exits 0.

## Cut-off prevention rule

Seats run with **no timeout by default**. A seat is cut off only if the user has set `cutoff_seconds` on that role. That is an explicit request, and it is logged with the trigger `user request`.

The engine also sets `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` so that the Claude harness never kills background work. If the harness still reports that it stopped background tasks, the cut-off is logged with the trigger `situation (runner)`.

A cut-off is handled like this:

1. It is written to `cutoffs.json` and `process.md`. Exception: a replacement reviewer's cut-off is logged in `process.md` as a review rejection but is not added to `cutoffs.json` in this version.
2. The output is marked incomplete.
3. What happens next depends on the output:
   - An incomplete build escalates the run.
   - An incomplete review, from a primary or replacement seat, is rejected and never passed to the judge as a full review; the seat is called again within its 3 attempts.
   - An incomplete judge verdict or agreement is rejected and the judge is called again within its 3 attempts.

   This version does not check the incomplete flag on QA/QC results or on the judge's bounded-fix acceptance; those are validated on content only.

## Live-access rule for workers

The worker environment is filtered by variable name; credentials under names that match the patterns below are removed.

Every seat call runs:

- as a fresh subprocess;
- in an empty temporary working directory;
- with the prompt on standard input, never on the command line.

The worker's environment is scrubbed. These variables are removed:

- any variable whose name contains `TOKEN`, `SECRET`, `KEY`, `PASSWORD`, `OAUTH` or `CREDENTIAL`, ignoring case;
- any variable whose name starts with `CLAUDE_CODE_`, `CLAUDECODE`, `ANTHROPIC_`, `GOOGLE_`, `GCLOUD`, `CLOUDSDK_`, `OPENAI_`, `GH_`, `GITHUB_`, `AZURE_`, `AWS_` or `GWS`;
- `CLAUDE_CONFIG_DIR` and `CODEX_HOME`.

A variable whose name matches none of these patterns passes through unfiltered.

A scrubbed variable reaches a worker only when an active user override names that worker's role and lists the variable in `access`. Two exceptions are set by the seat adapter after the scrub: the seat's checked profile folder (`CLAUDE_CONFIG_DIR` for claude, `CODEX_HOME` for codex), and `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` for claude always. A replacement seat gets the same access as the role it replaces.

Overrides come only from user-owned sources:

- the `--override` flag;
- the session and project settings files.

Agent-written sources are never used.

Seats are also isolated from local configuration:

- Claude seats run with no tools, no MCP servers (`--strict-mcp-config`) and no user settings, unless the role sets `setting_sources`.
- Codex seats run with `--ignore-user-config` and an explicit sandbox.

---
name: operationalize
description: "Turn a project into an operation: move its folder from 01.PROJECT to 10.OPERATION, turn its recipe into the standing procedure for each cycle, set its cadence and runner, give it an ON/OFF switch on the operations dashboard, and update PROJECT_INDEX.md. Use this skill whenever the user says 'operationalize', 'turn this project into an operation', 'make this an operation', 'make this recurring', 'this is ongoing now', 'run this every week', 'run this every month', 'put this on a schedule', 'keep this running', or any indication that one-time work has become maintenance or a repeating cycle. Closing a finished project is project-complete, not this skill. Always use this skill for the conversion — do not move a project into 10.OPERATION or add a dashboard row by hand without running this ceremony first."
---

# Operationalize Ceremony

This skill turns a project into an operation. Its counterpart is `projectify`, which goes the other way.

## Project or Operation

A **project** is a one-time endeavor with a firm deadline. An **operation** is ongoing work — maintenance, anything cyclical. Some work starts as a project and becomes an operation: building the weekly digest is a project; sending it every Monday is an operation.

An operation is a **switch**. `10.OPERATION/OPERATIONS_DASHBOARD.md` lists every operation with its switch, and a run goes ahead only when its row says `ON`.

```
10.OPERATION/
├── OPERATIONS_DASHBOARD.md                 the summary — one row and one switch per operation
└── {YYYYMMDD}_{type1}_{type2}_{name}/      the same folder, under the same name it had in 01.PROJECT
    ├── 10.INPUT/     what each cycle starts from
    ├── 20.PROCESS/   working files, and RECIPE.md — the procedure for one cycle
    └── 30.OUTPUT/    what each cycle delivers
```

## Ceremony Steps

Execute these steps in order. Nothing is written or moved before the confirmation in Step 4.

### Step 0: Check the Layout

`01.PROJECT/` must exist at the workspace root. If it is missing, the workspace is not on the project-first layout: stop and tell the user. `10.OPERATION/` and its dashboard may be missing; Step 5 creates them.

### Step 1: Identify the Project

Determine which project is being converted, and find its folder:

- In `01.PROJECT/` — continue. Show the project name and folder, its status and deadline in `PROJECT_INDEX.md`, and its latest RAM path, then ask: "Confirming: you're turning **{project name}** into an operation. Correct?"
- Already in `10.OPERATION/` — there is nothing to convert. Say so and stop.
- Not a project yet (loose files, or only an idea) — an operation is born as a project: setting it up is one-time work with a deadline. Offer `start-project`, and stop.

### Step 2: Is It an Operation?

Establish, from the conversation or by asking:

- **What repeats** — one cycle, in one sentence.
- **Cadence or trigger** — every Monday 08:00; on the 1st; whenever a new file arrives.
- **Runner** — what runs a cycle and on which device: a scheduled script (Windows Task Scheduler), a scheduled agent task, or an agent by hand when asked. Ask whether something is already running it today.
- **What one cycle takes in and delivers** — the files that arrive in `10.INPUT`, the files that land in `30.OUTPUT`.

If nothing repeats, this is not an operation. Stop, and point to `project-complete` if the work is finished. If only part of the project repeats, say which part and ask how to split it before going on.

### Step 3: Prepare (read only)

1. **The recipe.** An operation is a recipe run again and again. Read `20.PROCESS/RECIPE.md` if it exists, and note whether it is missing or older than the current outputs.
2. **The cycle section.** Draft the section that Step 5 will put directly under the recipe's header:

   ```markdown
   ## Each cycle
   - Gate: check the switch first; stop unless it is ON
   - Trigger: {cadence, or the event that starts a cycle}
   - Takes in: {what must be in `10.INPUT` before it starts}
   - Steps: {the step numbers below that repeat, in order}
   - Delivers: {the files in `30.OUTPUT`, with their naming pattern}
   - Proof it worked: {a check — one a script can run, where possible}
   - If it fails: {who is told, and what happens to the half-finished cycle}
   ```

3. **Still manual.** Per Execution Routing, a repeating step that a script could do is a script. List the repeating steps that are still done by hand or by judgment.
4. **The paths that will break.** Search for the project's current path — absolute, and `01.PROJECT/{folder_name}` — in the project's own scripts and recipe, in the workspace `scripts/` folder, and in every runner the user named. Record each hit with its file and line. A moved folder with a stale path in a scheduled script fails on a schedule, with nobody watching.

### Step 4: Present the Conversion Plan (Confirmation Gate)

> **Operationalize `{folder_name}`**
>
> - **One cycle:** {what repeats}
> - **Cadence:** {cadence or trigger}
> - **Runner:** {what runs it, on which device; whether it is running today}
> - **Recipe:** {exists / will be written} — gains the `## Each cycle` section, status `final`
> - **Move:** `01.PROJECT/{folder_name}/` → `10.OPERATION/{folder_name}/` (same name)
> - **Paths that will break:** {each hit, or "none found"}
> - **Still manual:** {repeating steps not yet code, or "none"}
> - **Switch at start:** {ON / OFF}
> - **Index:** PROJECT_INDEX.md status → `operation`
>
> Proceed?

Wait for the user's confirmation. This gate is never skipped: the move changes paths other things depend on.

### Step 5: Convert

In this order:

1. **Stop what is running.** If a runner already runs this work, disable it now (do not delete it), and wait for a running cycle to finish. Anything on another device is the user's to do, or to confirm.

2. **Write the recipe.** If `RECIPE.md` is missing or older than the outputs, write it first with the recipe procedure of the `cloud-sync` skill (`references/recipe.md`), including its reverse-engineering check; without that skill, write the steps and the reason for each, then work backwards from `30.OUTPUT` to confirm every output is explained. Then insert the `## Each cycle` section from Step 3 and set the recipe's `status` to `final`. If the user wants to skip the recipe, do not convert: the recipe is the operation; without it there is only a folder in a different place.

3. **Add the dashboard row, switched OFF.**
   `scripts/operation_switch.ps1 add {folder_name} -Cadence "{cadence}" -Runner "{runner}"`
   The script creates `10.OPERATION/` and the dashboard when they are missing. If the workspace has no `scripts/operation_switch.ps1`, copy this skill's `scripts/operation_switch.ps1` there first.

4. **Move the folder** `01.PROJECT/{folder_name}/` → `10.OPERATION/{folder_name}/`. Same name; nothing inside it changes. After the move, `10.INPUT/` has the operation-input access rule: read-only except for separately authorized writes. The project permission to create and edit input files does not carry into the operation. Deleting an input file or folder requires Max's explicit permission; approving conversion does not authorize deleting its inputs. If this session cannot move folders on the device (a cloud session), leave the move as one explicit instruction for the user or a local agent and **stop here**: the switch stays OFF and the runners stay disabled until the folder has moved. Go on to Step 6 and say what is still open.

5. **Fix the paths** found in Step 3. Anything outside the workspace needs the user's confirmation first.

6. **Put the gate in every runner, then enable it again.** A run checks its switch before it does anything else:
   - A PowerShell script — its first lines:
     ```powershell
     $global:LASTEXITCODE = 4
     & '{workspace}\scripts\operation_switch.ps1' check '{folder_name}'
     if ($LASTEXITCODE -eq 3) { exit 0 }                # switched OFF: stop quietly
     if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }    # cannot tell: stop, and let the scheduler show a failed run
     ```
   - A Python script — its first lines:
     ```python
     import subprocess, sys
     gate = subprocess.run(["powershell", "-NoProfile", "-File", r"{workspace}\scripts\operation_switch.ps1", "check", "{folder_name}"])
     if gate.returncode == 3:
         sys.exit(0)                    # switched OFF: stop quietly
     if gate.returncode != 0:
         sys.exit(gate.returncode)      # cannot tell: stop loudly
     ```
   - A scheduled agent task — its first instruction: "Read `10.OPERATION/OPERATIONS_DASHBOARD.md`. Continue only if exactly one row names `{folder_name}` and its Switch cell is `ON`. If it is `OFF`, stop quietly. In every other case — no row, two rows, a file you cannot read — stop and report that the switch could not be read."
   - An agent running a cycle by hand — it runs the same check first.

   Runs never write to the dashboard. It is one hand-edited, synced file; automated writers create conflict copies.

7. **Switch it ON**, if the plan said so: `scripts/operation_switch.ps1 on {folder_name}`.

### Step 6: RAM Checkpoint

Create a new RAM version recording the conversion: what one cycle is, the cadence, the runner and its device, where the gate was added, which paths were fixed, what is still manual, and anything left open (a deferred move, a runner on another device).

### Step 7: Update PROJECT_INDEX.md

The row stays — session skills find the RAM through it. Update:
- Status → `operation`
- Latest RAM path → the RAM from Step 6
- One-line summary → what one cycle is, and its cadence
- Deadline → empty

Do not copy the switch state here. The dashboard is the only place it lives.

### Step 8: Confirm

Present a concise summary:

- New location: `10.OPERATION/{folder_name}/` (or: the move that is still to be done)
- Switch: ON or OFF, and the two ways to flip it — edit the cell in `OPERATIONS_DASHBOARD.md`, or `scripts/operation_switch.ps1 on|off {folder_name}`
- Cadence and runner
- Where the gate was added, and any runner that still lacks it
- Paths fixed, and paths left for the user
- PROJECT_INDEX.md and RAM updated

## Edge Cases

- **The project cannot yet complete one cycle:** Finish it first, or convert with the switch OFF. An operation that has never run clean is not switched on.
- **A folder with this name already exists in `10.OPERATION/`:** Stop and ask.
- **The runner lives on another device:** Add the row here and hand the user the gate lines for that device. Say plainly that the runner is not gated until those lines are in place.
- **The workspace is not synced between devices (a local hub):** Its dashboard governs only what runs from that device. Say so when the runner is elsewhere.
- **The user wants to pause an operation:** That is the switch — `OFF`. Not this skill, and not `projectify`.
- **The dashboard has rows without folders, or folders without rows:** `scripts/operation_switch.ps1 status` lists them. A folder without a row never runs.

## Bundled resources

- `scripts/operation_switch.ps1` — the switchboard script, for a workspace that does not have it in its own `scripts/` folder yet (Step 5.3). Runs under Windows PowerShell 5.1 and PowerShell 7.

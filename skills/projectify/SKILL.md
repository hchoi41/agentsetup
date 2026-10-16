---
name: projectify
description: "Turn an operation into a project: switch it off, move its folder from 10.OPERATION to 01.PROJECT, take it off the operations dashboard, give it a firm deadline and acceptance criteria, and update PROJECT_INDEX.md. Use this skill whenever the user says 'projectify', 'turn this operation into a project', 'make this operation a project', 'this operation needs a deadline', 'make this a one-time project', 'wind this operation down', or any indication that ongoing, cyclical work has become a one-time endeavor with an end date. Starting new work as a project is start-project, not this skill; pausing an operation is its switch on the dashboard, not this skill. Always use this skill for the conversion — do not move an operation into 01.PROJECT or delete its dashboard row by hand without running this ceremony first."
---

# Projectify Ceremony

This skill turns an operation into a project. Its counterpart is `operationalize`, which goes the other way.

## Project or Operation

A **project** is a one-time endeavor with a firm deadline. An **operation** is ongoing work — maintenance, anything cyclical — with a switch on `10.OPERATION/OPERATIONS_DASHBOARD.md`.

An operation becomes a project when it gets an end: its last cycle is set, or it turns into a one-time piece of work — a rebuild, a migration, a wind-down — that finishes on a date. The folder moves from `10.OPERATION/` to `01.PROJECT/` and returns to date-first naming: `weekly_digest_20261017_v0.03` → `20261017_weekly_digest`, with the same `10.INPUT`, `20.PROCESS` and `30.OUTPUT` inside. Preserve the original creation date and registry identity. Record the last operation name/version in the recipe and RAM so re-operationalizing reuses v0.03, or advances to v0.04 if the operation was deliberately revised, rather than resetting.

## Ceremony Steps

Execute these steps in order. Nothing is written or moved before the confirmation in Step 4.

### Step 0: Check the Layout

`01.PROJECT/` and `10.OPERATION/` must both exist at the workspace root. If either is missing, stop and tell the user.

### Step 1: Identify the Operation

Determine which operation is being converted. It must be a folder in `10.OPERATION/`.

If the user points at something else — loose files, an idea, a folder in `01.PROJECT/` — this is the wrong skill. Say so: "projectify" means turning an operation into a project, and new work is started with `start-project`. Offer that instead; do not switch to it unasked.

Display to the user:
- Operation name and folder
- Its switch, cadence and runner, from the dashboard (`scripts/operation_switch.ps1 status`)
- Latest RAM path, from PROJECT_INDEX.md

Ask: "Confirming: you're turning **{operation name}** into a project. Correct?"

### Step 2: Is It a Project?

Establish, from the conversation or by asking:

- **The one-time outcome** — what will be finished.
- **The deadline** — a date. A project has a firm deadline; if there is none, ask. Do not invent one, and do not convert without it.
- **What happens to the cycle** — it stops for good, or it pauses until the project is done.

If the cycle must keep running while one-time work happens beside it, do not projectify. The operation stays where it is, and the one-time work is a new project (`start-project`) that names the operation as an input.

If the user only wants the operation to stop for a while, flip its switch `OFF`. That is all.

### Step 3: Prepare (read only)

1. **Draft the acceptance criteria.** A project is checked against them when it closes. Draft them the way `start-project` does: concrete, checkable statements covering completeness, correctness, audience fit, format, function and constraints, taken from a reference document, an earlier project, or the conversation. Confirm the list with the user. Step 5 saves it.
2. **Find the paths that will break.** Search for the operation's current path — absolute, and `10.OPERATION/{operation_folder}` — in its own scripts and recipe, in the workspace `scripts/` folder, and in every runner. Record each hit with its file and line.
3. **List the runners** — each scheduled task, script trigger or agent task, with its device.
4. **Calculate the project name** with the bundled or workspace `scripts/operation_name.ps1 -Action ToProject -FolderName "{operation_folder}"`. Its `target` is `{project_folder}`. The helper writes nothing. A valid legacy date-first operation keeps its existing name; record it as legacy-unversioned only if its history confirms it has never had an operation version; malformed names or uncertain creation dates need an explicit mapping. Check the target is absent and that dashboard/registry references identify this one operation. Draft the last operation folder/version record for the recipe's `## Operation identity` section and RAM; write it only after Step 4 approval, before removing the suffix.

### Step 4: Present the Conversion Plan (Confirmation Gate)

> **Projectify `{operation_folder}`**
>
> - **Outcome:** {what will be finished}
> - **Deadline:** {YYYY-MM-DD}
> - **Acceptance criteria:** `10.INPUT/{YYYYMMDD}_{slug}_acceptance_criteria_v{N}.md` — the specific write to operation input before the move
> - **The cycle:** {stops for good / pauses until the project is done}
> - **Runners:** {each one, with its device} — {deleted / disabled until the cycle resumes}
> - **Move and rename:** `10.OPERATION/{operation_folder}/` → `01.PROJECT/{project_folder}/`
> - **Identity:** {original creation date and last operation version retained in recipe/RAM}; registry slug preserved
> - **Paths that will break:** {each hit, or "none found"}
> - **Dashboard:** row removed
> - **Index:** PROJECT_INDEX.md status → `active`, deadline set
>
> Proceed?

Wait for the user's confirmation. This gate is never skipped.

### Step 5: Convert

In this order:

1. **Switch it OFF first** — `scripts/operation_switch.ps1 off {operation_folder}` — so no cycle starts in the middle of the move. If a cycle is running now, wait for it to finish.

2. **Disable the runners** in the plan. Delete a runner only when the cycle stops for good; when it only pauses, disable it. Anything on another device or outside the workspace is the user's to do, or to confirm. A runner left behind is stopped by its gate once the row is gone, but it keeps firing and failing; list any that remain.

3. **Save the acceptance criteria** inside the folder, before it moves, as
   `10.INPUT/{YYYYMMDD}_{slug}_acceptance_criteria_v{N}.md` — `N` is the next free version if an earlier criteria file exists. This specific write is part of the conversion plan confirmed in Step 4; operation inputs otherwise remain read-only. Once the folder is under `01.PROJECT/`, agents may create and edit its input files and subfolders. Deleting an input file or folder requires Max's explicit permission. Preserve prior criteria versions and source provenance.

4. **Preserve operation lineage and set the recipe's `status` to `provisional`** in `20.PROCESS/RECIPE.md`. Retain or add the `## Operation identity` section with the previous operation folder/version, semantic name and original creation date. If a recipe is missing, write this known identity record without inventing its procedure. The recipe is checked again when the project completes.

5. **Move and rename the folder** `10.OPERATION/{operation_folder}/` → `01.PROJECT/{project_folder}/`. Recheck that the source is the intended folder, both resolved paths are inside the workspace, and the destination is absent. Move the whole folder without merging or overwriting; preserve its contents. If this session cannot move folders on the device (a cloud session), leave the move as one explicit instruction for the user or a local agent and **stop here**: the row stays on the dashboard, switched OFF, until the folder has moved. Record the pending move in RAM, preserving the preconversion index status, deadline and current path. Do not perform the success status update in Step 7 until the move and live-reference repairs are verified. Any failure follows the same pending branch: keep runners disabled and the switch OFF, and report the exact remaining steps.

6. **Fix the live paths and identity references** found in Step 3, including registry roots/output paths and current index links. Keep the adopted registry canonical slug, retaining the old/new names as aliases for the same identity. Preserve historical transcripts, old RAM and outputs. Anything outside the workspace needs the user's confirmation first.

7. **Verify the move and repaired references** before removing the old dashboard row: the target exists, source is absent and registry/current paths resolve. Keep the row OFF and runners disabled if verification fails. **Remove the dashboard row** — `scripts/operation_switch.ps1 remove {operation_folder}`. Without the script, delete the row by hand.

### Step 6: RAM Checkpoint

Create a new RAM version recording the conversion: old/new folder names, preserved creation date and last operation version for the next operationalization, the outcome and deadline, what happened to the cycle, which runners were deleted or disabled and which remain, which paths were fixed, where the acceptance criteria are, and anything left open.

### Step 7: Update PROJECT_INDEX.md

Update the row:
- Status → `active`
- Latest RAM path → the RAM from Step 6
- One-line summary → the one-time outcome
- Deadline → the date from Step 2

If the operation has no row, add one, as `start-project` does.

### Step 8: Confirm

Present a concise summary:

- New location: `01.PROJECT/{project_folder}/` (or: the move that is still to be done)
- Deadline
- Runners deleted or disabled, and any still to handle
- Paths fixed, and paths left for the user
- Dashboard row removed; PROJECT_INDEX.md and RAM updated
- From here it is a project: `project-complete` closes it. If the cycle was only paused, `operationalize` puts it back when the project is done.

## Edge Cases

- **The user says "projectify" about something that is not an operation:** Say what the word means and offer `start-project` (Step 1).
- **The cycle must keep running:** Not this skill. The operation stays; the one-time work is a new project.
- **The user only wants a pause:** Flip the switch `OFF`.
- **The folder has no dashboard row:** It was already off — a folder without a row never runs. Skip Steps 5.1 and 5.7, and say so.
- **A folder with this name already exists in `01.PROJECT/`:** Stop and ask.
- **The semantic name no longer fits** (its `type1` verb described the cycle): Keep the semantic name unless the user asks to change it; the format still returns to date first. The date stays the day the work started.
- **Returning to operation later:** use the recorded previous operation name with ToOperation; reuse that version for an unchanged operation, or add `-IncrementVersion` for a deliberate revision, preserving the date and identity. Never silently restart at v0.01.

## Bundled resources

- `scripts/operation_name.ps1` — read-only conversion and version calculation; also installed at the workspace script root.

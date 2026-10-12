---
name: cloud-sync
description: "Publish a project or operation from the local-first hub to its OneDrive cloud counterpart: 10.INPUT and 30.OUTPUT are copied in full, whatever their size or file count, and 20.PROCESS goes up as its metadata plus a recipe — how the work was done and why — cross-checked by reverse engineering from the outputs. Use whenever the user wants to push, upload, sync, back up, publish, mirror, or put a project's inputs, outputs, working files, models, or logs on the cloud; mentions the OneDrive counterpart or cross-device / cross-agent access; asks 'what should I sync' or 'which files go to the cloud'; wants the recipe for a project written; or just finished a deliverable they want off the local hub. Distinct from session-handoff, which carries session RAM and continuity; cloud-sync carries the work itself. Always run it instead of hand-copying to OneDrive, so nothing in INPUT or OUTPUT is left behind, secrets never leak, and the working bulk stays local."
---

# Cloud Sync

Publish a project or operation from the **local-first hub** to its **OneDrive cloud counterpart**: everything that went in, everything that came out, and how the work was done.

## The rule

The folder a file sits in decides what happens to it. There are no size limits, no file-count limits, no version pruning and no pointer files.

| Folder | What reaches the cloud | Why |
| --- | --- | --- |
| `10.INPUT` | **Everything** — every file, any size, any number | What the work was built from cannot be regenerated |
| `30.OUTPUT` | **Everything** — every file, any size, any number | The deliverables are the point |
| `20.PROCESS` | **Not the files.** A manifest (the metadata) and a recipe (how it was done and why), cross-checked by reverse engineering | A future project needs the method, not the drafts |

Working files stay on this device. Anything in `20.PROCESS` that must survive this machine belongs in `30.OUTPUT` — say so before a machine is wiped or retired.

`start-project` looks up recipes from earlier work of the same `type1` and `type2`; `operationalize` turns a recipe into an operation's standing procedure.

## Relationship to sibling skills

- **`session-handoff`** carries the *thread* — RAM, `PROJECT_INDEX`, the `SESSION_HANDOFF.md` pointer. **`cloud-sync`** carries the *work*.
- **`project-complete`** closes a project and moves finished files into `30.OUTPUT`. cloud-sync is the natural next step.

At a finish line: `project-complete` → `cloud-sync` → (optionally) `session-handoff`.

## Workflow

```
resolve → dry run → settle what is held back → apply (the files) → write the recipe → apply again → reconcile
```

All commands below are this skill's `scripts/sync_project.ps1` (PowerShell 7). It defaults to a dry run. Pass `-SrcRoot` explicitly when `AGENT_HUB_ROOT` is not configured; do not guess a workspace.

### Step 0 — Resolve source and destination

- **Source:** the folder in the local hub — `01.PROJECT/<name>/` for a project, `10.OPERATION/<name>/` for an operation. Resolve the workspace root from `00.ABOUT/PROJECT_REGISTRY.json` (canonical slug + `root_paths`).
- **Destination:** the OneDrive workspace that **owns this work's domain**. The folder keeps the same name in the same place. Map by domain, then confirm on first run:
  - job applications / assignments → the job-search OneDrive workspace
  - financial models / market research → the modeling workspace
  - agent-setup / tooling → `980.agents_setup`
  - general / weekly / personal → the corresponding `000_*` workspace
- **Both sides must be on the `01.PROJECT` layout.** The script refuses a workspace that has no `01.PROJECT/`. Do not work around it, and never sync into another folder instead.
- **Collision guard.** If the same work already exists in the destination under a *different* folder name (cross-workspace drift is common), **stop and ask**: sync into that folder (`-DstName <its name>`), or rename one side first. Never create a parallel folder silently. Record the chosen destination so future syncs are unambiguous.

Read the source and destination AGENTS.md / CLAUDE.md before writing. A local `CHARTER.md` domain map, when present, helps choose the destination. External knowledge vaults retain their own roles and access rules; this skill cannot map them into hub folders or bypass either layout guard.

### Step 1 — Dry run

```
scripts/sync_project.ps1 -Project <name> -SrcRoot <hub> -DstRoot <OneDrive workspace>
```

It changes nothing. Show the user the plan it prints: what will be copied, what is already there, what is **not** copied and why, any blockers, and the three closing lines `FILES:`, `RECIPE:`, `RESULT:`.

### Step 2 — Settle what is held back

`references/exclude_and_secrets.md` lists the only things that are not copied. Each one is named in the plan. What needs a decision:

- **Possible secret.** A human decides, never the agent. Show the user the file name and the reason. A real secret: they remove it from the file, or move the file out of INPUT/OUTPUT. A false alarm: after the user says so for that exact file, re-run with `-Allow <relative path>`. The clearance is remembered in the hub and lapses when the file's content changes.
- **A name OneDrive refuses** (`.lock`, `_vti_` in the name, a leading or trailing space). Rename it in the hub.
- **The cloud copy is newer than the hub's.** Someone edited it there. Ask which one wins: copy the cloud version back into the hub by hand, or re-run with `-OverwriteNewer <relative path>` to let the hub's win.

A **blocker** (a link or junction, a path over OneDrive's limit, a folder in the way of a file, no space) stops the run before anything is written. Fix the cause; never work around one by leaving the file out.

### Step 3 — Apply: the files

Re-run with `-Apply`. The script copies `10.INPUT`, `30.OUTPUT` and the project's RAM files, writes `PROCESS_MANIFEST.json`, and verifies every file in the destination. It never deletes or renames anything there; a file that exists only in the cloud is reported, not removed.

The inputs and outputs are now safe. If the recipe is missing or out of date, `RESULT` says `INCOMPLETE` — that is expected at this point.

### Step 4 — Write or refresh the recipe

Skip this step when `RECIPE:` says `verified`. Otherwise follow `references/recipe.md`:

1. **Write `20.PROCESS/RECIPE.md`** — the steps, the reason for each, what was tried and dropped — from the RAM, the acceptance criteria, `PROCESS_MANIFEST.json` and the working files. `references/job_profiles.md` says what a recipe for this kind of work must record.
2. **Cross-check by reverse engineering.** Start from `30.OUTPUT` alone and work backwards: what must have been done to produce each file? Compare that reconstruction with the steps you wrote and fix every mismatch.
3. **Stamp it** with the `outputs_fingerprint` the script printed.

`status` in the recipe header says what the script holds it to:

- `provisional` — the project is still running. The recipe must exist; it is not yet held to the outputs.
- `final` — the project is completed (or is being operationalized). Every output must be accounted for and the stamp must match. Set it when `PROJECT_INDEX.md` says `completed`.
- An operation's recipe must exist; its outputs change every cycle, so it is not held to a stamp.

If a step cannot be repeated without a file that exists only in `20.PROCESS` (a script, a template, a prompt), put its content in the recipe or ask the user to move it to `30.OUTPUT`.

`-RecipeOnly` checks the recipe without syncing anything (no destination needed).

### Step 5 — Apply again

Run the same `-Apply` command once more. It copies nothing that is already there, sends the recipe up, and rewrites the report.

Report the three closing lines to the user as they are:

- `RESULT: OK` — every input and output is in the OneDrive folder on this computer and verified there, and the recipe is in order. The OneDrive app uploads from that folder on its own schedule.
- `RESULT: INCOMPLETE` — it ran, but something that belongs in the cloud is not there yet. Do not call it done.
- `RESULT: BLOCKED` / `FAILED` — nothing, or not everything, was written.

### Unload receipt

`20.PROCESS/SYNC_REPORT.md` is the unload receipt on both sides after an apply. Keep its path and actual `FILES:`, `RECIPE:` and `RESULT:` state in the project's RAM. It records copied/already-present counts, files held back, exclusions, newer-cloud collisions, cloud-only files, and failures. A dry run only previews those states and creates no receipt.

For an immutable closure record, after the final successful apply preserve the source receipt as `20.PROCESS/{YYYYMMDD}_{artifact_prefix}_unload_receipt_v{N}.md` without overwriting a prior version, and reference the source/destination `SYNC_REPORT.md` paths in RAM. This is a local audit artifact; ordinary PROCESS files do not sync. An archived receipt never proves later upload completion: record the user's OneDrive status separately.

Earlier receipts used auto-uploaded/manual-required/pointered categories. On this layout, report **verified local copy**, **held back / action required**, **excluded by rule**, and **collisions**. No item is sent to manual upload or converted to a pointer because of a size threshold.

### Step 6 — Reconcile

- **Destination `PROJECT_INDEX.md`:** update the row — status, deadline, latest RAM path (point at the synced RAM). Do **not** auto-create `PROJECT_REGISTRY.json` entries (curated manually) — propose them.
- **Operations:** the cloud copy is a record, not a runner. Give it a row in the destination's dashboard, switched OFF, naming where it really runs — with the destination workspace's own switch script:
  `<destination>/scripts/operation_switch.ps1 add <name> -IfMissing -Notes "runs from <hub device>; this copy is a record"`
- **On-device finish:** remind the user — in File Explorer, right-click the synced folder → **"Free up space"**, once OneDrive shows it as uploaded.

## Knobs

- `-Allow <path>[,<path>]` — copy a file the secret scan held back. Only after the user cleared that exact file.
- `-OverwriteNewer <path>[,<path>]` — let the hub's file replace a newer cloud copy. Only after the user chose.
- `-DstName <folder>` — the destination folder has another name.
- `-Slug <slug>` — the name the project's RAM files carry, when it is not the folder name without its date.
- `-Hash` — compare and verify by SHA-256. Slower, and it downloads online-only files in the destination to compare them; use it for the push that closes a project.
- `-RecipeOnly` — check the recipe; with `-Apply`, also refresh the manifest in the hub.

## Safety notes

- **Secrets never leave the local machine** unless a human cleared that exact file.
- **"Always sync" means complete, not forced.** If a file cannot go up, the run says so and stops or ends `INCOMPLETE` — it never quietly leaves the file out.
- **One direction:** local hub → OneDrive. Nothing in the destination is deleted.
- **Never** run dependency installs or generate caches inside OneDrive. Runtime folders and temporary working files belong in local `20.PROCESS` or the machine's configured runtime/cache area. The dry run warns about misplaced runtime folders; resolve their role before the authorized apply. Do not silently omit INPUT/OUTPUT content to hide a misplaced folder.
- **Project input access.** Agents may create and edit files and subfolders in `01.PROJECT/<project>/10.INPUT/`, preserving source provenance and prior versions when revising evidence. Deleting an input file or folder requires Max's explicit permission. The same named folder under `10.OPERATION/<operation>/` remains read-only except for a separately authorized write; do not inherit the project edit permission after conversion. Clearing a Windows Read-only attribute does not grant deletion permission.
- **Preserve output authority and required decisions.** Output access is unchanged. Do not move or prune source material, or rewrite a deliverable, merely to clear sync. Existing secret-clearance and newer-cloud decisions still apply. Rerun the dry run after an authorized input edit or other approved remediation.
- **Skill authority:** if this skill is edited, the unpacked source in the hub is canonical — edit there, then repackage (per `AGENTS.md`).

## Bundled resources

- `scripts/sync_project.ps1` — the sync engine. `tools/tests/test_sync_project.ps1` in the migration kit is its self-test.
- `references/exclude_and_secrets.md` — the only things not copied, the secret patterns, and the blockers. Read in Step 2.
- `references/recipe.md` — the recipe format, how to write it, and the reverse-engineering check. Read in Step 4.
- `references/job_profiles.md` — what a repeat needs, by kind of work. Read in Step 4.
- `evals/evals.json` — sample trigger/behaviour test prompts.

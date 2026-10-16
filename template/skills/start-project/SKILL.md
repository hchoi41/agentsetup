---
name: start-project
description: "Scaffold a new project (one-time work with a firm deadline): create its folder under 01.PROJECT with 10.INPUT, 20.PROCESS and 30.OUTPUT, register it in PROJECT_INDEX.md and PROJECT_REGISTRY.json, scan for existing artifacts and earlier recipes, and create the initial RAM. Use this skill whenever the user says 'start a project', 'create a project', 'new project', 'this is a project', 'a project is created', 'let's make this a project', 'spin up a project', 'project for this', 'these belong together as a project', 'this is one unit of idea', 'initiate project', 'crate project', 'make project', or any indication that a body of work should be tracked as a formal project. Also trigger when the user groups multiple artifacts under a shared concept and implies project-level tracking. To turn an existing operation into a project, use projectify instead. Always use this skill for project creation — do not create project folders, index entries, or RAM manually without running this ceremony first."
---

# Start Project Ceremony

This skill scaffolds a new project inside the workspace. It is the counterpart to `project-complete` — one opens the lifecycle, the other closes it.

## Project or Operation

A **project** is a one-time endeavor with a firm deadline. An **operation** is ongoing work — maintenance, anything cyclical — and lives in `10.OPERATION/`, not here.

- Work that will repeat still starts as a project: setting it up is one-time and has a deadline. Scaffold it here; once a cycle works, `operationalize` moves it to `10.OPERATION/`.
- Turning an existing operation into a project is `projectify`, not this skill.

## Model Recommendation

This ceremony is procedural — it follows a fixed sequence of file reads, folder creation, and structured writes. It does not require deep reasoning or complex synthesis. **Use Sonnet** (or the latest equivalent) when delegating this to a subagent. Opus is unnecessary here and costs more for no benefit. If running in the current session rather than as a subagent, this note serves as a record of the user's preference.

## Why This Exists

Projects in this workspace are more than folders. They carry identity (PROJECT_REGISTRY.json), runtime state (PROJECT_INDEX.md), working memory (RAM), and one folder that holds everything the project touches:

```
01.PROJECT/{YYYYMMDD}_{type1}_{type2}_{name}/
├── 10.INPUT/     what the work starts from — sources, the brief, acceptance criteria
├── 20.PROCESS/   the work in progress — drafts and working files
└── 30.OUTPUT/    what the project delivers — finished files only
```

Starting a project by hand risks missing one of these layers, which causes drift: orphan folders with no RAM, index entries pointing nowhere, or registry gaps that confuse future sessions and transcript ingest.

This ceremony ensures every new project starts with the full infrastructure in place, existing artifacts are cataloged, and the workspace's coordination files stay consistent.

## Ceremony Steps

### Step 0: Check the Layout

`01.PROJECT/` must exist at the workspace root. If it is missing, stop and tell the user: the workspace is not on the project-first layout. Do not create `01.PROJECT/` yourself — a new workspace is scaffolded with `deploy_new_project.ps1`, and an older one is moved over with `migrate_layout.py` (see `Legacy paths` in `PROJECT_GOVERNANCE.md`).

### Step 1: Determine the Project Identity

Figure out what the project is about from the conversation context. If the user has already named it or described the conceptual unit, extract that. If not, ask.

Build the **folder name** from four parts — `{YYYYMMDD}_{type1}_{type2}_{name}`:
- `YYYYMMDD` — the local date the project starts, unless the user explicitly supplies another project date
- `type1` — one verb for what this project is (`coach`, `apply`, `analyze`, `build`, `prepare`)
- `type2` — one theme keyword: the client, subject, or area (`acme`, `finance`, `home`)
- `name` — 2–4 words for this specific project
- Use `snake_case` throughout (lowercase, underscores for spaces)

Before proposing a name, list the folders already in `01.PROJECT/` and `10.OPERATION/` and reuse a `type1` and `type2` that are already in use whenever one fits. These two tokens are how a later project finds earlier work of the same kind, so `coach` and `coaching` must not both exist.

**Naming rules (inherited from CLAUDE.md):**
- No Windows-illegal characters: `< > : " / \ | ? *`
- No trailing periods or trailing spaces
- No reserved Windows device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`)
- Folder names are always in English
- If the resulting path would be excessively long (folder name > ~60 characters), shorten the slug while keeping the meaning clear

Present the suggestion to the user:

> "I'd name this project folder `20261010_coach_acme_onboarding`. Sound good, or do you want something different?"

**If the user says "auto", "you name it", "go ahead", or similar** — skip confirmation and use your suggested name directly. "Auto" covers the name, never the deadline: if no deadline is known yet, ask for it.

**If the user provides a name** — use their name, converting to snake_case if needed. Keep the date first; if their name has no `type1` or `type2`, propose them rather than dropping the pattern.

**Deadline.** A project has a firm deadline. Take the date from the conversation, or ask for it. If the user says there is no end date, or that the work repeats, it is not a project as described: the one-time part (setting it up, the first cycle) can be the project with its own deadline, and `operationalize` takes over afterwards. Do not invent a deadline.

**Earlier recipes of the same kind.** A finished project leaves a `RECIPE.md` in its `20.PROCESS/` — how the work was done and why. Search `01.PROJECT/*/20.PROCESS/RECIPE.md` and `10.OPERATION/*/20.PROCESS/RECIPE.md` for the header lines `- type1: {type1}` and `- type2: {type2}`; if no recipe matches both, take those that match `type1`. (Folders named before this convention carry their `type1` and `type2` only in those two lines.) Note the paths: the plan in Step 1b shows the count, and the RAM in Step 6 lists them. Do not copy the files.

Also derive:
- **display_name**: A human-readable title (e.g., "Acme Onboarding Coaching")
- **canonical_slug**: The folder name without the date prefix (e.g., `coach_acme_onboarding`) for a new project, unless the user specifies otherwise. When formalizing or resuming an existing project, preserve its adopted registry identity, even when that identity includes a date. Do not rename existing projects as part of this ceremony.
- **artifact_prefix**: The concise date-free semantic prefix for filenames (normally the new canonical slug). Write project artifacts as `{YYYYMMDD}_{artifact_prefix}_{content_type}_v{N}.{ext}`. The artifact date is the local day the artifact is written; it need not equal the project folder date. Fixed control filenames such as `RECIPE.md` follow their tool contract. In the templates below, `{slug}` is the registry identity and `{artifact_prefix}` is the filename prefix.
- **aliases**: 2–4 short alternate names someone might use to refer to this project in conversation

### Step 1b: Present the Project Plan (Confirmation Gate)

Before proceeding with any folder creation, registration, or RAM writes, present a lightweight project plan to the user and wait for confirmation. Project initiation is always a multistep task that benefits from explicit alignment before execution.

Present the plan in this format:

> **Project plan for `{folder_name}`**
>
> - **Goal:** {one-line objective inferred from context}
> - **Deadline:** {YYYY-MM-DD}
> - **Folder:** `01.PROJECT/{folder_name}/` with `10.INPUT/`, `20.PROCESS/`, `30.OUTPUT/`
> - **Registry:** will add entry to PROJECT_REGISTRY.json and PROJECT_INDEX.md
> - **Initial RAM:** `12.MEMORY_RAM/{YYYYMMDD}_{artifact_prefix}_ram_v1.md`
> - **Artifact prefix:** `{artifact_prefix}` (date-free; the artifact date comes first)
> - **Existing artifacts:** {count if any were detected, or "none found"}
> - **Earlier recipes of this kind:** {count, or "none found"}
> - **Open questions:** {anything unclear about scope, inputs, or deliverables}
>
> Proceed?

Rules:

- Wait for the user's confirmation before moving to Step 1c.
- If the user modifies the plan (renames, changes scope, adds inputs), update accordingly and re-confirm only if the change is structural. Minor wording tweaks do not need a second confirmation.
- If the user says "auto", "just do it", "go ahead", or similar — treat that as blanket confirmation and proceed through all remaining steps without further gates.
- The ceremony itself is the plan — no separate planning step is needed when starting a project.
### Step 1c: Draft Acceptance Criteria

Before building infrastructure, establish the acceptance criteria — the grading rubric this project's deliverables will be checked against at completion. This document is created collaboratively and stored in the project's `10.INPUT/` — it is the brief the work starts from.

**Determine the source:**

Ask the user which of these applies:

1. **Reference document available** — the user has an existing document that defines success (e.g., a job description, a rubric, a brief, an RFP). If so, use it as the primary source. Extract the criteria from it, reformat into a checklist, and confirm with the user.
2. **Past project precedent** — the user has done a similar project before and can point to an existing acceptance criteria file to adapt. If so, copy and modify it for this project's specifics.
3. **Build from conversation** — the project is novel or ambiguous enough that criteria need to be developed together. Ask targeted questions about what "done right" looks like: what must the deliverables contain, what quality bar applies, who is the audience, what would cause a rejection or redo.

**Checklist structure:**

The acceptance criteria document should cover (as applicable to the project):

- **Content completeness** — does the deliverable contain everything it needs to?
- **Accuracy and correctness** — are facts, data, calculations, and references correct?
- **Audience fit** — is the tone, depth, and framing appropriate for the intended reader or reviewer?
- **Format and presentation** — does it meet any structural, formatting, or template requirements?
- **Functional requirements** — does it work as intended (if applicable: code runs, links resolve, formulas compute)?
- **Constraints and compliance** — does it satisfy any external rules, regulations, or guidelines?
Each criterion should be a concrete, checkable statement — not vague aspirations. "Includes salary benchmarking data for 3+ comparable roles" is good. "Is high quality" is not.

**File output:**

Save the acceptance criteria as (Step 3 creates the folder — write the file then if it does not exist yet):
`01.PROJECT/{folder_name}/10.INPUT/{YYYYMMDD}_{artifact_prefix}_acceptance_criteria_v1.md`

In the updated project layout, agents may create and edit files and subfolders in `01.PROJECT/{folder_name}/10.INPUT/`. Deleting an input file or folder requires Max's explicit permission. Preserve source provenance and prior versions when revising evidence. Write the acceptance criteria after their content has been agreed; this agreement is about the criteria, not an exception to a read-only input role. This permission does not change operation-input access or authorize writes in an unmigrated workspace.

Format:

```markdown
# Acceptance Criteria — {display_name}

**Created:** {YYYY-MM-DD}
**Source:** {reference document name / past project / conversation}
**Version:** v1

---

## Criteria

- [ ] {criterion 1}
- [ ] {criterion 2}
- [ ] ...

## Notes
{Any context about how these criteria should be interpreted, edge cases, or known gaps}
```

**Confirm with the user** before proceeding. If the user modifies the list, update the file and re-confirm only if the change is structural.

Record the acceptance criteria file path in the RAM created at Step 6 under "Key Inputs / Referenced Files."

### Step 2: Check for Collisions

Before creating anything, verify:

1. **Slug collision**: Check PROJECT_REGISTRY.json and PROJECT_INDEX.md for an existing project with the same slug or display name. If found, alert the user and ask how to proceed — rename the new project, or merge into the existing one.
2. **Folder collision**: Check whether `01.PROJECT/{folder_name}/` already exists. If it does, that's not necessarily an error — the user may have created the folder manually before formalizing. Note what is in it and proceed. Do not overwrite or recreate existing folders. Then check `10.OPERATION/` and registry aliases for the same semantic identity, including `{slug}_YYYYMMDD_v0.01` (and later versions) as well as legacy date-first names: compare identity without the operation date/version suffix. If there is one, stop and ask — the user may want `projectify` (turn that operation into a project) rather than a second, parallel folder. Do not create a new identity merely because the folder format changed.

### Step 3: Create the Project Folder

Create `01.PROJECT/{folder_name}/` and, inside it:
- `10.INPUT/`
- `20.PROCESS/`
- `30.OUTPUT/`

Skip creation for any folder that already exists.

### Step 4: Scan for Existing Artifacts

Scan `01.PROJECT/{folder_name}/20.PROCESS/` for any files already present. This commonly happens when the user has been working before formalizing the project.

Also check `30.OUTPUT/` — if it already has files, those are likely prior completed deliverables that should appear in the RAM's canonical artifacts table — and `10.INPUT/`, where the user may already have dropped source material.

If the conversation provides clues about source material that sits outside the project folder, note its path as a key reference. Do not guess blindly — only record references you can confirm from context.

For each artifact found, capture:
- Filename
- A one-line description (infer from the filename and a quick read of the first ~20 lines)
- Version if detectable from the filename convention (`_v1`, `_v2`, etc.)
- Which folder it lives in (`10.INPUT`, `20.PROCESS`, or `30.OUTPUT`)

This catalog goes into the initial RAM (Step 6).

### Step 5: Register the Project

Both writes below target `00.ABOUT`, which is normally read-only. CLAUDE.md grants a standing exception for PROJECT_INDEX.md. For PROJECT_REGISTRY.json, **this skill's invocation constitutes the user's standing authorization** — the user approved this write pattern by installing the skill. No additional confirmation is needed.
**PROJECT_INDEX.md** (in `00.ABOUT`):

Add a new row, one cell per column of the table's own header:
- `project_name`: the display_name
- `status`: `active`
- `latest_ram_path`: the RAM path you're about to create in Step 6
- `must_read`: empty, unless the user named a folder to load at startup
- `current_state_summary`: a one-line summary of the project's starting state
- `deadline`: the date from Step 1 (`YYYY-MM-DD`). It is the last column. If the table has no `deadline` column, add it as the last column — header, separator row, and an empty cell at the end of every existing row — and change nothing else in those rows.

If PROJECT_INDEX.md doesn't exist, create it in `00.ABOUT` with the header `| project_name | status | latest_ram_path | must_read | current_state_summary | deadline |` — this is a standing exception to the ABOUT read-only rule per CLAUDE.md.

**PROJECT_REGISTRY.json** (in `00.ABOUT`):

Add a new entry to the `projects` array:
```json
{
  "canonical_slug": "{slug}",
  "display_name": "{display_name}",
  "root_paths": ["{parent_workspace_folder}"],
  "aliases": ["{alias1}", "{alias2}"],
  "title_keywords": ["{keyword1}", "{keyword2}"]
}
```

If PROJECT_REGISTRY.json doesn't exist, note this to the user but do not create it from scratch — registry creation is a curated action per CLAUDE.md. The project can still function without a registry entry; it just won't be available for automated identity resolution.

### Step 6: Create Initial RAM

Ensure `12.MEMORY_RAM/` exists. If it doesn't, create it.

Create the first RAM checkpoint at:
`12.MEMORY_RAM/{YYYYMMDD}_{artifact_prefix}_ram_v1.md`

Use the standard RAM structure:
```markdown
# RAM — {display_name}

**Project slug:** {slug}
**Artifact prefix:** {artifact_prefix}
**Date:** {YYYY-MM-DD}
**Deadline:** {YYYY-MM-DD}
**Version:** v1

---

## Current Objective
{What this project aims to produce — infer from context}

## Current Status
{What exists so far — artifacts found in Step 4, decisions made in the conversation}

## Decisions Already Made
{Any choices the user has made that should carry forward}

## Unresolved Questions / Blockers
{Open items, if any}

## Key Inputs / Referenced Files
{Files in `01.PROJECT/{folder_name}/10.INPUT/` that feed this project, including the acceptance criteria; earlier recipes found in Step 1}

## Output Locations
- **Working drafts:** `01.PROJECT/{folder_name}/20.PROCESS/`
- **Final versions:** `01.PROJECT/{folder_name}/30.OUTPUT/`

## Canonical Artifacts
| artifact | path | version | date |
|---|---|---|---|
| ... | ... | ... | ... |

## HDD Pointers
None yet.

## Recommended Next Action
{What should happen next — infer from context}
```

Fill this with as much context as the conversation provides. The RAM should be useful enough that a future session can pick up the project cold. If the conversation is thin on context, fill what you can and mark gaps explicitly (e.g., "Objective: TBD — user has not specified deliverables yet").

### Step 7: Confirm Creation

Present a concise summary:

- Project name and folder name
- Deadline
- Folders created (or already existed)
- Artifacts found (count and brief list)
- Earlier recipes of the same kind (count and paths), if any
- PROJECT_INDEX.md updated
- PROJECT_REGISTRY.json updated (or noted as absent)
- RAM location

Keep it short. The user just declared a project — they want to see it's set up and move on.

## Edge Cases

- **`01.PROJECT/` is missing:** Stop at Step 0 and tell the user. Do not scaffold into any other folder, and do not create `01.PROJECT/` yourself.
- **No deadline, or the work repeats:** It is an operation, or not yet a project. Offer to scope the one-time part as the project (Step 1). Do not invent a deadline.
- **User says "projectify":** That word belongs to the `projectify` skill, which turns an operation into a project. Do not treat the word as a request to start a project.
- **Project folder exists but one of the three subfolders is missing:** Create only the missing one. Don't treat it as an error.
- **PROJECT_INDEX.md doesn't exist:** Create it in `00.ABOUT` per the standing exception in CLAUDE.md.
- **PROJECT_REGISTRY.json doesn't exist:** Note it to the user. Do not create it — that's a curated document.
- **12.MEMORY_RAM folder doesn't exist:** Create it before writing the RAM file.
- **User provides a name with spaces or mixed case:** Convert to `snake_case`.
- **User provides a non-English name:** Convert to an English `snake_case` equivalent. Confirm with the user before proceeding.
- **User provides a name with illegal characters:** Strip or replace them silently. Mention what you changed.
- **Folder name would exceed ~60 characters:** Shorten the slug, keeping the meaning clear. Confirm with the user.
- **Slug collision with existing project:** Alert the user. Options: rename, merge, or abort.
- **Loose files sit at the root of `01.PROJECT/` (not in any project folder):** If the conversation makes clear these belong to the new project, note them in the RAM under "Unresolved Questions" as candidates to move. Do not move files without user confirmation — they may have been deliberately placed at root level.
- **User says "this is a project" with no prior context:** Create the scaffold with minimal RAM. Mark objective and status as TBD. A project with infrastructure and sparse RAM is better than artifacts with no tracking.
- **The project already has a mature folder and possibly existing RAM:** This is a retroactive formalization. Check for existing RAM in `12.MEMORY_RAM/`. If found, do not overwrite — instead, update PROJECT_INDEX and PROJECT_REGISTRY to point at the existing RAM. Only create new RAM if none exists.
- **Source material sits outside the project folder (another project's `10.INPUT/`, a folder elsewhere in the workspace):** Only reference paths you can confirm from the conversation, and do not copy or move them without confirmation. If ambiguous, list candidates in the RAM under "Unresolved Questions" rather than guessing.
- **Locally customized roles or external knowledge vaults:** Read the owning workspace's AGENTS.md and CLAUDE.md before writing. Preserve valid local access restrictions. This ceremony requires the project-first layout; a local role mapping never authorizes bypassing Step 0 or imposing hub folder roles on a separate vault.

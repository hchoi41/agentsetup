name: start-project
description: "Scaffold a new project within the workspace — create paired folders in OUTPUT_ROUGH and OUTPUT_FINAL, register it in PROJECT_INDEX.md and PROJECT_REGISTRY.json, scan for existing artifacts, and create the initial RAM. Use this skill whenever the user says 'start a project', 'create a project', 'new project', 'this is a project', 'a project is created', 'let's make this a project', 'spin up a project', 'project for this', 'these belong together as a project', 'this is one unit of idea', 'initiate project', 'crate project', 'projectify', 'make project', or any indication that a body of work should be tracked as a formal project. Also trigger when the user groups multiple artifacts under a shared concept and implies project-level tracking. Always use this skill for project creation — do not create project folders, index entries, or RAM manually without running this ceremony first."
---

# Start Project Ceremony

This skill scaffolds a new project inside the workspace. It is the counterpart to `project-complete` — one opens the lifecycle, the other closes it.

## Model Recommendation

This ceremony is procedural — it follows a fixed sequence of file reads, folder creation, and structured writes. It does not require deep reasoning or complex synthesis. **Use Sonnet** (or the latest equivalent) when delegating this to a subagent. Opus is unnecessary here and costs more for no benefit. If running in the current session rather than as a subagent, this note serves as a record of the user's preference.

## Why This Exists

Projects in this workspace are more than folders. They carry identity (PROJECT_REGISTRY.json), runtime state (PROJECT_INDEX.md), working memory (RAM), and a paired folder structure (OUTPUT_ROUGH for drafts, OUTPUT_FINAL for finished work). Starting a project by hand risks missing one of these layers, which causes drift: orphan folders with no RAM, index entries pointing nowhere, or registry gaps that confuse future sessions and transcript ingest.

This ceremony ensures every new project starts with the full infrastructure in place, existing artifacts are cataloged, and the workspace's coordination files stay consistent.
## Ceremony Steps

### Step 1: Determine the Project Identity

Figure out what the project is about from the conversation context. If the user has already named it or described the conceptual unit, extract that. If not, ask.

Derive a **folder name** from the project concept:
- Use `snake_case` (lowercase, underscores for spaces)
- Aim for 3–5 words — descriptive but concise
- The same folder name is used in both `11.OUTPUT_ROUGH` and `03.OUTPUT_FINAL`

**Naming rules (inherited from CLAUDE.md):**
- No Windows-illegal characters: `< > : " / \ | ? *`
- No trailing periods or trailing spaces
- No reserved Windows device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`)
- Folder names are always in English
- If the resulting path would be excessively long (folder name > ~60 characters), shorten the slug while keeping the meaning clear

Present the suggestion to the user:

> "I'd name this project folder `claude_cowork_ai_layers`. Sound good, or do you want something different?"

**If the user says "auto", "you name it", "go ahead", or similar** — skip confirmation and use your suggested name directly. Proceed to Step 2 without waiting.

**If the user provides a name** — use their name, converting to snake_case if needed.
Also derive:
- **display_name**: A human-readable title (e.g., "Claude Cowork and AI Layers")
- **canonical_slug**: Same as folder name unless the user specifies otherwise
- **aliases**: 2–4 short alternate names someone might use to refer to this project in conversation

### Step 1b: Present the Project Plan (Confirmation Gate)

Before proceeding with any folder creation, registration, or RAM writes, present a lightweight project plan to the user and wait for confirmation. Project initiation is always a multistep task that benefits from explicit alignment before execution.

Present the plan in this format:

> **Project plan for `{folder_name}`**
>
> - **Goal:** {one-line objective inferred from context}
> - **Folder pair:** `11.OUTPUT_ROUGH/{folder_name}/` + `03.OUTPUT_FINAL/{folder_name}/`
> - **Registry:** will add entry to PROJECT_REGISTRY.json and PROJECT_INDEX.md
> - **Initial RAM:** `12.MEMORY_RAM/{slug}_ram_{YYYYMMDD}_v1.md`
> - **Existing artifacts:** {count if any were detected, or "none found"}
> - **Open questions:** {anything unclear about scope, inputs, or deliverables}
>
> Proceed?

Rules:

- Wait for the user's confirmation before moving to Step 1c.
- If the user modifies the plan (renames, changes scope, adds inputs), update accordingly and re-confirm only if the change is structural. Minor wording tweaks do not need a second confirmation.
- If the user says "auto", "just do it", "go ahead", or similar — treat that as blanket confirmation and proceed through all remaining steps without further gates.
- The ceremony itself is the plan — no separate planning step is needed when starting a project.
### Step 1c: Draft Acceptance Criteria

Before building infrastructure, establish the acceptance criteria — the grading rubric this project's deliverables will be checked against at completion. This document is created collaboratively and stored alongside the project's working files.

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

Save the acceptance criteria as:
`11.OUTPUT_ROUGH/{folder_name}/{slug}_acceptance_criteria_{YYYYMMDD}_v1.md`

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
2. **Folder collision**: Check whether `11.OUTPUT_ROUGH/{folder_name}/` or `03.OUTPUT_FINAL/{folder_name}/` already exists. If one or both exist, that's not necessarily an error — the user may have created folders manually before formalizing. Note which exist and proceed. Do not overwrite or recreate existing folders.
### Step 3: Create the Folder Pair

Create the project folder in both locations:
- `11.OUTPUT_ROUGH/{folder_name}/`
- `03.OUTPUT_FINAL/{folder_name}/`

Skip creation for any folder that already exists.

### Step 4: Scan for Existing Artifacts

Scan `11.OUTPUT_ROUGH/{folder_name}/` for any files already present. This commonly happens when the user has been working before formalizing the project.

Also check `03.OUTPUT_FINAL/{folder_name}/` — if it already has files, those are likely prior completed deliverables that should appear in the RAM's canonical artifacts table.

If the conversation provides clues about which INPUT subfolder feeds this project, note the INPUT path as a key reference. Do not guess blindly — only record INPUT references you can confirm from context.

For each artifact found, capture:
- Filename
- A one-line description (infer from the filename and a quick read of the first ~20 lines)
- Version if detectable from the filename convention (`_v1`, `_v2`, etc.)
- Which folder it lives in (OUTPUT_ROUGH or OUTPUT_FINAL)

This catalog goes into the initial RAM (Step 6).

### Step 5: Register the Project

Both writes below target `00.ABOUT`, which is normally read-only. CLAUDE.md grants a standing exception for PROJECT_INDEX.md. For PROJECT_REGISTRY.json, **this skill's invocation constitutes the user's standing authorization** — the user approved this write pattern by installing the skill. No additional confirmation is needed.
**PROJECT_INDEX.md** (in `00.ABOUT`):

Add a new row:
- `project_name`: the display_name
- `status`: `active`
- `latest_ram_path`: the RAM path you're about to create in Step 6
- `current_state_summary`: a one-line summary of the project's starting state

If PROJECT_INDEX.md doesn't exist, create it in `00.ABOUT` — this is a standing exception to the ABOUT read-only rule per CLAUDE.md.

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
`12.MEMORY_RAM/{slug}_ram_{YYYYMMDD}_v1.md`

Use the standard RAM structure:
```markdown
# RAM — {display_name}

**Project slug:** {slug}
**Date:** {YYYY-MM-DD}
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
{INPUT files that feed this project, if known}

## Output Locations
- **Working drafts:** `11.OUTPUT_ROUGH/{folder_name}/`
- **Final versions:** `03.OUTPUT_FINAL/{folder_name}/`

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
- Folders created (or already existed)
- Artifacts found (count and brief list)
- PROJECT_INDEX.md updated
- PROJECT_REGISTRY.json updated (or noted as absent)
- RAM location

Keep it short. The user just declared a project — they want to see it's set up and move on.
## Edge Cases

- **Folder exists in one location but not the other:** Create only the missing one. Note the asymmetry but don't treat it as an error.
- **PROJECT_INDEX.md doesn't exist:** Create it in `00.ABOUT` per the standing exception in CLAUDE.md.
- **PROJECT_REGISTRY.json doesn't exist:** Note it to the user. Do not create it — that's a curated document.
- **12.MEMORY_RAM folder doesn't exist:** Create it before writing the RAM file.
- **User provides a name with spaces or mixed case:** Convert to `snake_case`.
- **User provides a non-English name:** Convert to an English `snake_case` equivalent. Confirm with the user before proceeding.
- **User provides a name with illegal characters:** Strip or replace them silently. Mention what you changed.
- **Folder name would exceed ~60 characters:** Shorten the slug, keeping the meaning clear. Confirm with the user.
- **Slug collision with existing project:** Alert the user. Options: rename, merge, or abort.
- **Artifacts already exist in OUTPUT_ROUGH root (not in the project subfolder):** If the conversation makes clear these belong to the new project, note them in the RAM under "Unresolved Questions" as candidates to move. Do not move files without user confirmation — they may have been deliberately placed at root level.
- **User says "this is a project" with no prior context:** Create the scaffold with minimal RAM. Mark objective and status as TBD. A project with infrastructure and sparse RAM is better than artifacts with no tracking.
- **The project already has a mature folder and possibly existing RAM:** This is a retroactive formalization. Check for existing RAM in `12.MEMORY_RAM/`. If found, do not overwrite — instead, update PROJECT_INDEX and PROJECT_REGISTRY to point at the existing RAM. Only create new RAM if none exists.
- **Multiple INPUT subfolders could feed the project:** Only reference INPUT paths you can confirm from the conversation. If ambiguous, list candidates in the RAM under "Unresolved Questions" rather than guessing.
- **Workspace uses non-standard folder labels:** Map actual on-disk folders to roles per CLAUDE.md before writing. Use the actual paths, not the preferred labels.
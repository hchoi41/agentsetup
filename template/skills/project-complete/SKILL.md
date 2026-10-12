---
name: project-complete
description: "Formally sign off a project and trigger wiki knowledge extraction. Use this skill whenever the user says 'project complete', 'sign off project', 'close project', 'finish project', 'project done', 'wrap up project', 'move to final', 'deliverables are done', 'archive this project', 'project finished', 'done with project', or any indication that a project has reached completion and deliverables should move to its 30.OUTPUT folder. This is the formal ceremony that triggers wiki knowledge extraction from the project's 10.INPUT and RAM. Work that will keep running on a cycle is not closed here — that is the operationalize skill. Always use this skill for project closure — do not close a project or update PROJECT_INDEX status to completed without running this ceremony first."
---

# Project Completion Ceremony

This skill formalizes the closure of a project and triggers knowledge extraction into the WIKI. It is the formal mechanism for Trigger A of the Wiki Processing Protocol defined in CLAUDE.md.

## Model Recommendation

This ceremony is procedural — it follows a fixed sequence of file reads, folder checks, and structured writes. It does not require deep reasoning or complex synthesis. **Use the latest Sonnet model** when delegating this to a subagent. Opus is unnecessary here and costs more for no benefit. If running in the current session rather than as a subagent, this note serves as a record of the user's preference.

## Why This Exists

Project completion is a natural reflection point. The work is done, the deliverables are finalized, and there's an opportunity to extract lasting knowledge before context is lost. Without a formal ceremony, valuable insights, decisions, and conceptual connections stay buried in RAM and INPUT — never entering the persistent knowledge library.

This skill ensures every project closure is also a knowledge harvest. It also enforces the complete shutdown sequence: deliverable verification, knowledge extraction, RAM finalization, and index update. Skipping steps here creates drift between the knowledge base and reality.
## Ceremony Steps

Execute these steps in order. Confirm with the user at each decision point — this is a ceremony, not a background job.

### Step 1: Identify the Project

Determine which project is being closed. If context makes it obvious, confirm. Otherwise ask.

Verify against `PROJECT_INDEX.md` in `00.ABOUT` and `PROJECT_REGISTRY.json` (if it exists).

Display to the user:
- Project name
- Current status in PROJECT_INDEX.md
- Latest RAM path

Ask: "Confirming: you're signing off **{project name}**. Correct?"

If the project doesn't exist in PROJECT_INDEX.md, something is wrong — investigate before proceeding.

The project's folder is `01.PROJECT/{folder_name}/`. If the workspace has no `01.PROJECT/`, it is not on the project-first layout: stop and tell the user.

If the work is not ending but will keep running on a cycle, this is not a closure — it is `operationalize`. Say so and stop.

### Step 2: Deliverable Verification

Check the project's `20.PROCESS/` for its deliverables.

Ask the user:
- "Have all deliverables been moved to `30.OUTPUT`? Or should I help identify what needs to move?"
- If deliverables need moving, list them and confirm before proceeding.

Do not move files without explicit confirmation. The user may have already handled this. Only finished files go to `30.OUTPUT/`; drafts and superseded versions stay in `20.PROCESS/`.

### Step 2b: Acceptance Criteria Review

Before proceeding to knowledge extraction, check the project's deliverables against the acceptance criteria established at project start.

**Locate the acceptance criteria file:**

Look for `*acceptance_criteria*.md` in `01.PROJECT/{folder_name}/10.INPUT/` (a project scaffolded before the layout change may have it in `20.PROCESS/`). If the file is referenced in the project's RAM under "Key Inputs / Referenced Files," use that path directly.

**If no acceptance criteria file exists:**

This can happen for projects that predate the acceptance criteria step or were scaffolded informally. In this case:
- Note the gap to the user: "No acceptance criteria were set at project start."
- Ask: "Would you like to do a quick retrospective check before closing? I can draft criteria from the project's RAM and we can evaluate against them — or we can skip and close as-is."
- If the user opts to skip, proceed to Step 2.5.
**Run the review:**

For each criterion in the checklist:

1. Read the relevant deliverable(s) in the project's `30.OUTPUT/` (and `20.PROCESS/` for anything not yet moved).
2. Assess whether the criterion is met, partially met, or not met.
3. For each assessment, provide a brief evidence note — what specifically in the deliverable satisfies (or fails to satisfy) the criterion.

**Present the results:**

```markdown
## Acceptance Criteria Review — {display_name}

**Date:** {YYYY-MM-DD}
**Criteria file:** {path to acceptance criteria}

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | {criterion text} | Met / Partially met / Not met | {brief note} |
| 2 | ... | ... | ... |

**Summary:** {X of Y criteria met. Overall assessment.}

**Items requiring attention:** {list any not-met or partially-met criteria}
```

**Save the report** as:
`01.PROJECT/{folder_name}/20.PROCESS/{YYYYMMDD}_{artifact_prefix}_acceptance_review_v1.md`

Resolve `artifact_prefix` from the existing RAM or adopted naming convention; for a new project it is normally the date-free canonical slug. Preserve existing registry identities and acceptance-criteria filenames. The lookup above accepts both current date-first and earlier filename ordering.

**Decision gate:**

- If all criteria are met → proceed to Step 2.5.
- If any criteria are not met or partially met → present the gaps to the user and ask: "Some criteria aren't fully met. Want to address these before closing, or close the project as-is with these noted?"
- If the user wants to address gaps → pause the ceremony. The project stays active until the gaps are resolved and the user re-triggers closure.
- If the user accepts the gaps → note them in the final RAM and proceed.
### Step 2.5 - Load/Unload & Archive

Before wiki extraction, offer to publish the finished work to its OneDrive domain through `cloud-sync` (engine: `skills/cloud-sync/scripts/sync_project.ps1`). Copy only within the user's confirmed publication scope; existing in-session authorization covers that scope.

1. Confirm the project identity and that finals are in `01.PROJECT/{folder_name}/30.OUTPUT/`.
2. Resolve the owning OneDrive domain from the local `CHARTER.md` domain map when present and the cloud-sync domain map. Settle a new destination or collision before copying. Both workspaces must have `01.PROJECT/`.
3. Run the cloud-sync dry run with explicit `-SrcRoot` and `-DstRoot`; present copied/already-present counts, held-back files, decisions required, exclusions, and collisions. Dry run creates no receipt or destination files.
4. On the confirmed plan, apply with `-Hash`, finish the recipe, then apply again. Use `20.PROCESS/SYNC_REPORT.md` on both sides as the unload receipt. It records the actual result; `OK` verifies the local OneDrive copy, not completed upload by the OneDrive app.
5. Report every incomplete item. The project-first policy copies all `10.INPUT` and `30.OUTPUT` content regardless of size: the earlier 25 MB manual-upload rule and pointer/pruning behavior do not apply. Runtime dependencies and drafts remain in `20.PROCESS`; review misplaced runtime warnings before publication.
6. Record the unload result, source and destination receipt paths, destination folder, and OneDrive upload status in final RAM. If publication is declined or incomplete, record that state explicitly; do not imply a completed cloud backup.

Continue with wiki extraction. After Step 8, refresh the final RAM and final recipe in the already authorized destination as described there.

### Step 3: Knowledge Extraction Scan

Read the project's latest RAM. Scan the project's `10.INPUT/` too — it holds the material the work was built from. Project input files and subfolders may be created and edited, preserving source provenance and prior versions when revising evidence. Deleting an input file or folder requires Max's explicit permission; project closure or archive language alone does not supply that permission.

Look for candidates worth extracting into wiki pages:
- Named concepts, frameworks, or models referenced in RAM
- Code sections, regulations, or standards central to the work
- People, organizations, or entities that appeared across multiple artifacts
- Decisions or insights with value beyond this specific project
- Any `[[wikilinks]]` in RAM that are currently red links (no page exists yet)

For each candidate, assess:
- **Already has a wiki page?** Flag for update, not creation.
- **Meets the page creation threshold** (2+ project references or 3+ mentions across distinct artifacts)? If not but the user explicitly wants it, create anyway.
- **Cross-project value?** If it's purely project-specific detail with no broader relevance, it belongs in the archive, not the wiki.

### Step 4: Propose Wiki Pages

Present two lists:

**Pages to create:**
- `[[Entity Name]]` — one-line rationale

**Pages to update:**
- `[[Existing Page]]` — what new information this project adds

Ask: "Here's what I'd extract into the WIKI. Want to add, remove, or modify anything?"

If no wiki-worthy knowledge is found, say so — not every project produces lasting cross-project knowledge. Skip to Step 6.
### Step 5: Process Approved Pages

For each approved page:

1. Create (or update) the page in `15.WIKI/processed/` with proper frontmatter:
   ```yaml
   ---
   uid: YYYYMMDD-HHMM
   created: YYYY-MM-DD
   updated: YYYY-MM-DD
   tags: [relevant, tags]
   aliases: [alternate names]
   source: project name
   ---
   ```
2. Write a concise **Summary**, **Key Points**, and **Connections** section.
3. Use `[[wikilinks]]` throughout to connect to existing pages.
4. Scan existing wiki pages for opportunities to add backlinks to the new page.
5. Check whether any existing MOC in `15.WIKI/processed/MOC/` should link to this page.
6. If a new MOC is warranted (5+ related pages with no navigation hub), propose it to the user.

### Step 6: Update log.md

Append a processing entry to `15.WIKI/log.md`:

```markdown
## YYYY-MM-DD — Trigger A: Project Completion

- **Project:** {project name}
- **Pages created:** [[Page 1]], [[Page 2]]
- **Pages updated:** [[Page 3]]
- **MOCs updated:** [[MOC Name]]
- **Notes:** {any context about the extraction}
```

If no pages were created or updated, log that too — the absence of extractable knowledge is still worth recording.
### Step 7: Final RAM Checkpoint

Create a final RAM version for the project with status "completed."

Check whether 3+ RAM versions exist for this project. If so, trigger RAM Consolidation per the protocol in CLAUDE.md:
- Merge into a single consolidated RAM preserving all still-valid decisions.
- Move prior RAM versions to `13.MEMORY_HDD/capsules/{project}/`.
- Verify all file references against disk before finalizing.

### Step 8: Update PROJECT_INDEX.md

Update the project's entry:
- Status → `completed`
- Latest RAM path → final (or consolidated) RAM
- One-line summary → update to reflect completion

If Step 2.5 included publication, refresh the recipe to `final`, cross-check and stamp it against `30.OUTPUT`, then rerun the cloud-sync dry run and apply with `-Hash` within the same approved scope. This publishes the final RAM and final recipe after closure. Record the resulting `SYNC_REPORT.md` receipt and report any incomplete cloud work separately from project closure.

### Step 9: Confirm Closure

Present a summary:
- Acceptance criteria result (X/Y met, or "no criteria set")
- Deadline in PROJECT_INDEX.md against today's date (met, or late by N days)
- Deliverables location (`01.PROJECT/{folder_name}/30.OUTPUT/`)
- Wiki pages created/updated (count and names)
- MOCs updated (if any)
- Final RAM location
- PROJECT_INDEX.md status
- Unload status and receipt path from Step 2.5 and the post-closure refresh; if declined or incomplete, name the outstanding cloud-sync step. A completed receipt includes the full `10.INPUT` and `30.OUTPUT` copies and a final, verified `20.PROCESS/RECIPE.md`.

Ask: "Project **{project name}** is now formally closed. Anything else before we move on?"

## Edge Cases

- **Deliverables not ready:** Pause at Step 2. Help the user finish before proceeding. Do not skip deliverable verification.
- **No wiki-worthy knowledge:** That's fine. Skip Steps 4-5, note "No wiki extraction" in log.md and final summary.
- **`10.INPUT/` is empty:** Work from RAM alone for the knowledge scan.
- **The work will keep running (maintenance, a repeating cycle):** Don't close it. Use `operationalize`.
- **The workspace has no `01.PROJECT/`:** Stop at Step 1 and tell the user.
- **No RAM exists:** Create one final RAM summarizing the project before closing, then proceed.
- **15.WIKI or subfolders missing:** Create the folder hierarchy (`15.WIKI/`, `15.WIKI/processed/`, `15.WIKI/processed/MOC/`) before writing wiki pages.
- **Multiple projects closing at once:** Run the ceremony sequentially for each project. Do not batch them — each deserves its own scan and confirmation.

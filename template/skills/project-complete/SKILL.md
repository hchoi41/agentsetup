name: project-complete
description: "Formally sign off a project and trigger wiki knowledge extraction. Use this skill whenever the user says 'project complete', 'sign off project', 'close project', 'finish project', 'project done', 'wrap up project', 'move to final', 'deliverables are done', 'archive this project', 'project finished', 'done with project', or any indication that a project has reached completion and deliverables should move to OUTPUT_FINAL. This is the formal ceremony that triggers wiki knowledge extraction from the project's INPUT and RAM. Always use this skill for project closure — do not close a project or update PROJECT_INDEX status to completed without running this ceremony first."
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

### Step 2: Deliverable Verification

Check `11.OUTPUT_ROUGH` for the project's deliverables.

Ask the user:
- "Have all deliverables been moved to `03.OUTPUT_FINAL`? Or should I help identify what needs to move?"
- If deliverables need moving, list them and confirm before proceeding.

Do not move files without explicit confirmation. The user may have already handled this.

### Step 2b: Acceptance Criteria Review

Before proceeding to knowledge extraction, check the project's deliverables against the acceptance criteria established at project start.

**Locate the acceptance criteria file:**

Look for `{slug}_acceptance_criteria_*.md` in `11.OUTPUT_ROUGH/{folder_name}/`. If the file is referenced in the project's RAM under "Key Inputs / Referenced Files," use that path directly.

**If no acceptance criteria file exists:**

This can happen for projects that predate the acceptance criteria step or were scaffolded informally. In this case:
- Note the gap to the user: "No acceptance criteria were set at project start."
- Ask: "Would you like to do a quick retrospective check before closing? I can draft criteria from the project's RAM and we can evaluate against them — or we can skip and close as-is."
- If the user opts to skip, proceed to Step 3.
**Run the review:**

For each criterion in the checklist:

1. Read the relevant deliverable(s) in `11.OUTPUT_ROUGH/{folder_name}/` and `03.OUTPUT_FINAL/{folder_name}/`.
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
`11.OUTPUT_ROUGH/{folder_name}/{slug}_acceptance_review_{YYYYMMDD}_v1.md`

**Decision gate:**

- If all criteria are met → proceed to Step 3.
- If any criteria are not met or partially met → present the gaps to the user and ask: "Some criteria aren't fully met. Want to address these before closing, or close the project as-is with these noted?"
- If the user wants to address gaps → pause the ceremony. The project stays active until the gaps are resolved and the user re-triggers closure.
- If the user accepts the gaps → note them in the final RAM and proceed.
### Step 3: Knowledge Extraction Scan

Read the project's latest RAM. If INPUT still has material used during the project, scan it too.

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

### Step 9: Confirm Closure

Present a summary:
- Acceptance criteria result (X/Y met, or "no criteria set")
- Deliverables location in OUTPUT_FINAL
- Wiki pages created/updated (count and names)
- MOCs updated (if any)
- Final RAM location
- PROJECT_INDEX.md status

Ask: "Project **{project name}** is now formally closed. Anything else before we move on?"

## Edge Cases

- **Deliverables not ready:** Pause at Step 2. Help the user finish before proceeding. Do not skip deliverable verification.
- **No wiki-worthy knowledge:** That's fine. Skip Steps 4-5, note "No wiki extraction" in log.md and final summary.
- **INPUT already emptied:** Work from RAM alone for the knowledge scan.
- **No RAM exists:** Create one final RAM summarizing the project before closing, then proceed.
- **15.WIKI or subfolders missing:** Create the folder hierarchy (`15.WIKI/`, `15.WIKI/processed/`, `15.WIKI/processed/MOC/`) before writing wiki pages.
- **Multiple projects closing at once:** Run the ceremony sequentially for each project. Do not batch them — each deserves its own scan and confirmation.
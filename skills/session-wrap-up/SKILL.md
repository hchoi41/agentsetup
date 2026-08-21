---
name: wrap-up
description: "Wrap up the current session and prepare for continuation in a new chat — on this computer OR a different machine/device. Use this skill whenever the user says 'wrap up', 'hand off', 'session handoff', 'save session', 'context is running out', 'running low on context', 'prepare for continuation', 'continue in new chat', 'continue on another device', 'switch machines', 'move to my laptop', or any indication that the current session needs to be closed out for continuation elsewhere. Also trigger when the user mentions context window limits, token limits, needing a fresh chat, or moving to another computer."
---

# Session Wrap-Up

This skill captures the current session's state so the next session can reconstruct it seamlessly — **including a fresh session on a different machine or device.**

## Shared Reference

The full methodology, edge cases, and native-systems table live in the `session-handoff` skill. Read `session-handoff/SKILL.md` if you need deeper context on the layered persistence design. This skill executes **Phase 1: WRAP-UP** from that document.

## Cross-Device Principle (read first)

Cowork does **not** sync session state across devices. Auto-memory, session transcripts, and the live task sidebar exist only on the machine that ran the session. The **only** thing that reaches another computer is the OneDrive-synced workspace (the role folders: `00.ABOUT`, `12.MEMORY_RAM`, `13.MEMORY_HDD`, …).

So the rule for this skill is: **everything needed to resume must land in the synced workspace.** Device-local stores (auto-memory, transcripts) are accelerators that may be absent on another machine — never let them be the sole carrier of any unique piece of context. Use workspace-role-relative paths (e.g. `12.MEMORY_RAM/...`), never machine-absolute paths, so pointers survive on another device.

## Procedure

Execute these steps in order.

### Step 1: Identify the Project

Determine which project the session has been working on. Use conversation context first. If unclear, check `PROJECT_INDEX.md` in the `ABOUT` role (`00.ABOUT`).

Confirm with the user: "Wrapping up session for **{project name}**. Correct?"

### Step 2: Capture Task State

Capture the current TodoWrite task list. The live sidebar is **device-local** and does not travel to another machine, so it must be written into the synced RAM (Step 4) and SESSION_HANDOFF.md (Step 6).

Record the full task list with statuses:
- All `completed` tasks (so the user can see what was already done)
- All `in_progress` tasks (the interrupted work)
- All `pending` tasks (the remaining plan)

Use this format:

```
## Task Snapshot
- [x] Completed task description
- [~] In-progress task description (was actively being worked on)
- [ ] Pending task description
```

If the productivity plugin's `TASKS.md` file exists in the workspace, update it too.

### Step 3: Session Summary

Produce a structured summary of what happened this session. Scan the full conversation history and extract:

**What was accomplished:**
- Deliverables created or modified (with file paths)
- Decisions made and their rationale
- Problems solved

**What changed:**
- Files created, edited, or moved (list actual role-relative paths)
- Any configuration or settings changes
- Any external actions taken (emails sent, calendar events, etc.)

**What's still open:**
- Unfinished work and its current state
- Blockers or questions that need answers
- Items the user said they'd handle themselves

**Recommended next action:**
- The single most important thing to do when resuming

Keep it factual and concise — structured sections, no narrative prose. Dense enough that someone reading it cold on another computer could resume the work.

### Step 4: Update RAM

Read the project's latest RAM file (find the path via `PROJECT_INDEX.md`).

Create a new RAM version following the workspace naming convention:
`{project}_ram_{YYYYMMDD}_v{N}.md`

The new RAM should:
- Carry forward any still-valid context from the previous RAM version
- Incorporate the session summary (Step 3) and the Task Snapshot (Step 2)
- Update all sections: Current Objective, Current Status, Decisions Already Made, Unresolved Questions, Key Inputs, Output Locations, Canonical Artifacts, HDD Pointers, Recommended Next Action
- Include a `## Session Handoff Note` section at the top (just below the header) with:
  - Date and approximate session duration
  - One-line session summary
  - Pointer to the previous RAM version it supersedes

Save to `12.MEMORY_RAM` using role-relative paths. Do not overwrite the previous RAM version.

### Step 5: Update PROJECT_INDEX.md

Update the project's row in `PROJECT_INDEX.md`:
- Point `latest_ram_path` to the new RAM file (role-relative)
- Update `current_state_summary` to reflect post-session state

### Step 6: Write the Cross-Device Handoff Pointer

Write a single, stable, **always-overwritten** file at `00.ABOUT/SESSION_HANDOFF.md`. This is the fixed entry point a fresh session on **any device** reads first — it needs zero local state and never changes path, so resume never has to guess where the latest handoff is.

```markdown
---
name: Session Handoff Pointer
description: Cross-device resume pointer — always reflects the most recent wrap-up. Read this FIRST when resuming on any machine.
updated: {YYYY-MM-DD HH:MM} {timezone}
device: {hostname or OS, if known}
---

# Latest Session Handoff

**Project:** {project_name}
**Latest RAM:** {role-relative path, e.g. 12.MEMORY_RAM/{project}_ram_{YYYYMMDD}_v{N}.md}
**Status:** {one-line status}
**Recommended next action:** {single next step}

## Task Snapshot
- [x] ...
- [~] ...
- [ ] ...

## Open Items
- ...

## Session Summary
{the dense structured summary from Step 3 — accomplished / changed / still open}

## Resume Instructions
On any device, once OneDrive has finished syncing, start a new chat and say
"Resume {project_name}". This file plus the RAM above are sufficient to
reconstruct context with no local session state.
```

If multiple projects were touched this session, stack each under its own `# Latest Session Handoff` block, most recent first.

### Step 7: Write to Auto-Memory (device-local convenience — optional)

Auto-memory loads before any skill is invoked, so it speeds up resume **on the same machine**. It does **not** reach another device, so only ever *duplicate* what already lives in SESSION_HANDOFF.md + RAM — never put unique context here.

**File:** `project_{slug}.md`

```markdown
---
name: {project_name} session handoff
description: Last session context for {project_name} — resume point, open items, and next action
type: project
---

Last session: {date}
Project: {project_name}
Synced handoff: 00.ABOUT/SESSION_HANDOFF.md
RAM: {role-relative path to latest RAM}
Status: {one-line status}
Next action: {recommended next action}
Open items: {brief list}
```

Also update `MEMORY.md` if a new file was created. Skip this whole step without ceremony if auto-memory is unavailable — the synced tier already holds everything.

### Step 8: Sync with Productivity Plugin (if active)

If the `productivity:task-management` or `productivity:memory-management` skills are available:
- Update `TASKS.md` (if it exists in the workspace) with the current task snapshot so it persists and syncs
- Update the plugin's `memory/` directory with session-specific context (e.g., shorthand or acronyms used this session)

Optional — skip if the plugin is not in use.

### Step 9: Confirm and Close

Present the user with:
1. A brief confirmation of what was saved and where — name the synced files: `00.ABOUT/SESSION_HANDOFF.md` and the new RAM path.
2. The exact phrase to use in the new session to resume (e.g., "Resume {project_name}").
3. **A cross-device reminder:** the handoff lives in OneDrive, so it works on this computer or any other — **but only after OneDrive finishes syncing.** Ask the user to confirm the workspace has synced before switching machines.
4. Any manual actions to take before closing (e.g., "Save the file open in your editor").

Say: "Session wrapped to the synced workspace. Once OneDrive has synced, start a new chat **on any device** and say **'Resume {project_name}'** to pick up where we left off."

## Edge Cases

**Multiple projects were active:**
Create separate RAM updates for each project touched, and stack each under its own block in `SESSION_HANDOFF.md`.

**No project context (exploratory session):**
Skip the project-specific RAM update. Still write a general summary to `SESSION_HANDOFF.md` (and, if available, auto-memory as `session_summary_{YYYYMMDD}.md`, type `project`).

**OneDrive may not have synced yet:**
The handoff is only portable once OneDrive uploads it. If the user is about to switch machines, remind them to let sync finish first.

## Model Recommendation

Use Sonnet if delegating to a subagent. The wrap-up phase benefits from running in the main session where the full conversation history is available.

---
name: session-handoff
description: "Manage session continuity when context runs out OR when moving to a different machine/device. Two phases: WRAP-UP (end of current session) and RESUME (start of new session). Use whenever the user says 'wrap up', 'hand off', 'session handoff', 'save session', 'context is running out', 'prepare for continuation', 'continue in new chat', 'continue on another device', 'switch machines', 'pick up where we left off', 'resume', 'continue from last session', 'where were we', 'what was I working on', 'reload context', 'catch me up', or any sign that either (a) the current session needs wrapping up for continuation elsewhere, or (b) a new session needs to restore context from a previous one. Also trigger on mentions of context window limits, token limits, a fresh chat, or moving to another computer. Always use this skill for session transitions — do not manually summarize sessions or update RAM for handoff without running this ceremony first."
---

# Session Handoff

This skill manages the transition between Cowork sessions — whether the new session is on the **same machine** (context window ran out) or a **different machine/device**. It ensures no context is lost and the new session feels like a seamless continuation of the old one.

## Why This Exists

Cowork sessions have finite context windows. When a session runs long — especially during deep project work — the context fills up and a new chat is needed. Separately, the user may simply switch computers (desktop → laptop, work → home). Without a formal handoff, the new session starts cold: it has to re-read files, re-derive decisions, and the user has to re-explain what they were doing. That friction breaks flow and wastes the new session's context on catch-up.

This skill solves that by creating a structured handoff at the end of the dying session and a structured bootstrap at the start of the fresh one.

## Cross-Device Continuity (the core constraint)

**Cowork does not synchronize session state across devices.** A handoff is only as portable as the place it is stored. Persistence mechanisms split into two tiers:

**Synced (cross-device) — the source of truth.** These live in the OneDrive-synced workspace and reach any machine the user signs into once OneDrive finishes syncing:
- **RAM** (`12.MEMORY_RAM`) — the primary structured handoff
- **SESSION_HANDOFF.md** (`00.ABOUT`) — a single, stable, fixed-path pointer to the latest resume point (introduced by this skill)
- **PROJECT_INDEX.md** (`00.ABOUT`) — project discovery + RAM pointer
- **HDD** (`13.MEMORY_HDD`) — deep history
- **TASKS.md** (productivity plugin, if committed to the workspace)

**Device-local (same-machine only) — optional accelerators.** These exist only on the computer that ran the session and are normally **absent on a different device**:
- **Cowork auto-memory** (`.auto-memory/`)
- **Session transcripts** (`list_sessions` / `read_transcript`)
- **TodoWrite** live task sidebar

**The governing rule:** everything required to resume must be written to the **Synced** tier. Device-local stores may duplicate that information for speed, but must never be the *sole* carrier of any unique piece of context. On resume, treat the Synced tier as authoritative and the device-local tier as a bonus that is silently skipped when missing (the normal cross-device case).

## Detecting the Phase

This skill operates in one of two phases depending on context:

**WRAP-UP phase** — The user is closing out an active session. Trigger words: "wrap up", "hand off", "save session", "context running out", "prepare for continuation", "continue in new chat", "continue on another device", "switching machines".

**RESUME phase** — The user is in a fresh session and wants to pick up where they left off. Trigger words: "resume", "continue", "pick up where we left off", "where were we", "what was I working on", "reload context", "catch me up".

If ambiguous, ask: "Are we wrapping up this session for handoff, or resuming from a previous one?"

---

## Phase 1: WRAP-UP

The wrap-up phase captures the current session's state into the **Synced** tier so any next session — same machine or different device — can reconstruct it. Execute these steps in order.

### Step 1: Identify the Project

Determine which project the session has been working on. Use conversation context first. If unclear, check `PROJECT_INDEX.md` in the `ABOUT` role (`00.ABOUT`).

Confirm with the user: "Wrapping up session for **{project name}**. Correct?"

### Step 2: Capture Task State

Capture the current TodoWrite task list state. The live sidebar is **device-local** and does not travel to another machine, so it must be written into the Synced tier (RAM in Step 4 and SESSION_HANDOFF.md in Step 6).

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

Produce a structured summary of what happened in the current session. This is the core handoff artifact. Scan the full conversation history and extract:

**What was accomplished:**
- Deliverables created or modified (with file paths)
- Decisions made and their rationale
- Problems solved

**What changed:**
- Files created, edited, or moved (list actual paths)
- Any configuration or settings changes
- Any external actions taken (emails sent, calendar events, etc.)

**What's still open:**
- Unfinished work and its current state
- Blockers or questions that need answers
- Items the user said they'd handle themselves

**Recommended next action:**
- The single most important thing to do when resuming

Keep it factual and concise — structured sections, no narrative prose. Dense enough that someone reading it cold on another computer could resume the work, short enough to not waste context in the new session.

### Step 4: Update RAM

Read the project's latest RAM file (find the path via `PROJECT_INDEX.md`).

Create a new RAM version following the workspace naming convention:
`{project}_ram_{YYYYMMDD}_v{N}.md`

The new RAM should:
- Carry forward any still-valid context from the previous RAM version
- Incorporate the session summary from Step 3 and the Task Snapshot from Step 2
- Update all sections: Current Objective, Current Status, Decisions Already Made, Unresolved Questions, Key Inputs, Output Locations, Canonical Artifacts, HDD Pointers, Recommended Next Action
- Include a `## Session Handoff Note` section at the top (just below the header) with:
  - Date and approximate session duration
  - One-line session summary
  - Pointer to the previous RAM version it supersedes

Save to `12.MEMORY_RAM` using **workspace-role-relative paths** (e.g. `12.MEMORY_RAM/...`), not machine-absolute paths — absolute paths break when the workspace opens under a different drive letter or user folder on another device. Do not overwrite the previous RAM version.

### Step 5: Update PROJECT_INDEX.md

Update the project's row in `PROJECT_INDEX.md`:
- Point `latest_ram_path` to the new RAM file (role-relative)
- Update `current_state_summary` to reflect post-session state

### Step 6: Write the Cross-Device Handoff Pointer

Write a single, stable, **always-overwritten** file at `00.ABOUT/SESSION_HANDOFF.md`. This is the fixed entry point a fresh session on **any device** reads first — it requires zero local state and never changes path, so resume never has to guess where the latest handoff is.

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

If multiple projects were touched this session, list each under its own `# Latest Session Handoff` block, most recent first.

### Step 7: Write to Auto-Memory (device-local convenience — optional)

Auto-memory loads before any skill is invoked, so writing here speeds up resume **on the same machine**. It does **not** reach another device, so it must only ever *duplicate* what already lives in SESSION_HANDOFF.md + RAM — never hold unique context.

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

Also update the `MEMORY.md` index if a new file was created. Skip this whole step without ceremony if auto-memory is unavailable — the Synced tier already holds everything.

### Step 8: Sync with Productivity Plugin (if active)

If the `productivity:task-management` or `productivity:memory-management` skills are available:
- Update `TASKS.md` (if it exists in the workspace) with the current task snapshot so it persists on disk and syncs
- Update the productivity plugin's `memory/` directory with session-specific context (e.g., shorthand or acronyms used this session)

Optional — skip if the plugin is not in use.

### Step 9: Confirm and Close

Present the user with:
1. A brief confirmation of what was saved and where (name the synced files: SESSION_HANDOFF.md + the RAM path)
2. The exact phrase to use in the new session to resume (e.g., "Resume {project_name}")
3. **A cross-device reminder:** the handoff lives in OneDrive, so it works on this computer or any other — **but only after OneDrive finishes syncing.** Ask the user to confirm the workspace has synced before switching machines.
4. Any manual actions to take before closing (e.g., "Save the file open in your editor")

Say: "Session wrapped to the synced workspace. Once OneDrive has synced, start a new chat **on any device** and say **'Resume {project_name}'** to pick up where we left off."

---

## Phase 2: RESUME

The resume phase reconstructs context in a fresh session — including a fresh session on a **different machine**. Maximum context recovery, minimum token spend. Read the **Synced** tier as the source of truth; treat the **device-local** tier as optional. Execute in order.

### Step 1: Identify the Project

1. Read `00.ABOUT/SESSION_HANDOFF.md` first — its fixed path means you can always find the latest resume point with no local state. It names the project and the latest RAM.
2. If that file is absent, fall back to `PROJECT_INDEX.md` for active projects.
3. If the user named a project, use that.
4. If multiple active projects exist (or SESSION_HANDOFF.md lists several), ask which one to resume.

### Step 2: Load the Context Stack

Read in this order. The **synced** layers are authoritative and should be read on every resume. The **device-local** layers are accelerators — read them only if present, and skip them silently if not (the normal case on a different device).

**SYNCED — source of truth (always read):**

- **Layer 1 — SESSION_HANDOFF.md** (`00.ABOUT`): the stable pointer. Gives project, latest RAM path, task snapshot, open items, next action.
- **Layer 2 — RAM** (`12.MEMORY_RAM`): read the latest RAM file named by Layer 1 / PROJECT_INDEX. The primary, complete context source.
- **Layer 3 — PROJECT_INDEX.md** (`00.ABOUT`): confirms the RAM pointer and current-state summary; resolves ambiguity.
- **Layer 4 — HDD** (`13.MEMORY_HDD`): only if RAM flags gaps or the user asks for deeper history.

**DEVICE-LOCAL — optional accelerators (read only if present):**

- **Layer 5 — Auto-memory** (`.auto-memory/`): `project_{slug}.md`, plus any `feedback_*.md` / `user_*.md`. On a different device this is normally empty — that is **expected, not an error**. Do not block or warn; the Synced tier already carries the resume content.
- **Layer 6 — Prior session transcript** (`list_sessions` + `read_transcript`): pull the tail of the previous session (last 15-20 messages) to recover conversational tone and micro-context. Only meaningful on the same machine that ran the prior session. Read only if: the RAM has a Session Handoff Note, a recent matching session exists, and the user hasn't said to skip it. Absent on another device — skip gracefully.

### Step 3: Restore Task List

Rebuild the TodoWrite list from the `## Task Snapshot` in SESSION_HANDOFF.md (or RAM):
- Previously `completed` → `completed`
- Previously `in_progress` → `pending` (interrupted, not finished)
- Previously `pending` → `pending`

This restores the visible sidebar — which never traveled across devices on its own. If there's no snapshot, skip; the briefing will orient the user. Sync `TASKS.md` if present.

### Step 4: Present the Briefing

Concise — orientation, not a report:

```
## Resuming: {Project Name}

**Where we left off:** {1-2 sentences from the Session Handoff Note / SESSION_HANDOFF.md}

**Key decisions still in effect:**
- {decision 1}
- {decision 2}

**Open items:**
- {item 1}
- {item 2}

**Recommended next action:** {the single next step}

Ready to continue. What would you like to tackle?
```

If you resumed on a **different device** (device-local layers were empty), say so briefly so the user knows the conversational tail wasn't available — e.g., "Resumed from the synced workspace (different machine), so I have the full project state but not the prior chat's exact wording." If a transcript *was* available, weave in any smoothing context ("You mentioned wanting to revisit X").

### Step 5: Confirm and Proceed

Wait for the user to confirm or redirect:
- Confirm → proceed normally
- Redirect → update working context
- Ask for more history → read HDD or older RAM versions

Once confirmed, the skill is done. Normal work resumes.

---

## Edge Cases

**No RAM exists for the project:**
Fall back to SESSION_HANDOFF.md → auto-memory → PROJECT_INDEX summary → ask the user. Create a RAM file as part of the resume so the next handoff has a proper starting point.

**Multiple projects were active in the previous session:**
Wrap-up creates separate RAM updates and stacks each under its own block in SESSION_HANDOFF.md. Resume presents a choice if the user doesn't specify.

**Resuming on a different device than the wrap-up:**
This is a primary supported case. The device-local tier (auto-memory, transcripts) will be empty — that is expected. Proceed confidently from SESSION_HANDOFF.md + RAM. Do not treat the missing local layers as an error or re-derive context the synced files already hold.

**OneDrive hasn't finished syncing:**
If SESSION_HANDOFF.md is missing, older than the user expects, or its `updated` timestamp predates the work the user describes, the workspace may still be syncing on this device. Tell the user, and offer to wait and re-read rather than rebuilding context from scratch. (When reading via the file tools, a cloud-only file is fetched on demand; if a bash command can't see it, it may not be materialized yet.)

**The user wants to wrap up without a project (exploratory / cross-project session):**
Skip the project-specific RAM update. Write a general session summary to SESSION_HANDOFF.md (and, if available, auto-memory as `session_summary_{YYYYMMDD}.md`, type `project`).

**RAM is stale (handoff happened many sessions ago):**
If the Session Handoff Note is dated more than a few days ago, flag it: "The last formal handoff was on {date}. Some context may have changed since then. Want me to scan for more recent activity before we proceed?"

## Native Systems Used

The handoff deliberately layers multiple persistence mechanisms so that no single point of failure loses context. The **Scope** column is the cross-device contract:

| System | Scope | Phase | Purpose |
|--------|-------|-------|---------|
| **RAM** (`12.MEMORY_RAM`) | Synced (cross-device) | Both | Primary structured handoff — decisions, status, next actions |
| **SESSION_HANDOFF.md** (`00.ABOUT`) | Synced (cross-device) | Both | Stable fixed-path pointer to the latest resume point; read first on resume |
| **PROJECT_INDEX.md** (`00.ABOUT`) | Synced (cross-device) | Both | Project discovery and RAM pointer |
| **HDD** (`13.MEMORY_HDD`) | Synced (cross-device) | Resume | Deep history fallback, only when RAM points to it |
| **TASKS.md** (productivity plugin) | Synced if committed to workspace | Both | Optional — persists the task snapshot on disk |
| **Auto-memory** (`.auto-memory/`) | Device-local (same machine) | Both | Optional accelerator that loads before any skill; must only duplicate synced content |
| **TodoWrite** (Cowork native) | Device-local (same machine) | Both | Live task sidebar; captured into the synced tier because it does not travel |
| **Session transcripts** (`list_sessions` + `read_transcript`) | Device-local (same machine) | Resume | Optional — recovers conversational tone; absent on another device |
| **CLAUDE.md / AGENTS.md** (workspace instructions) | Synced (cross-device) | Resume | Auto-loaded every session — implicit, not directly invoked |

The layering is intentional: the Synced tier guarantees portability to any device, while the device-local tier adds speed and conversational nuance when the user happens to be on the same machine.

## Model Recommendation

Both phases are procedural — a fixed sequence of reads and structured writes. **Use Sonnet** if delegating to a subagent. The wrap-up phase benefits from running in the main session (where the full conversation history is available), not as a subagent.

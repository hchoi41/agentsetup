---
name: resume-session
description: "Resume work from a previous session by restoring context, tasks, and briefing — even when the previous session ran on a different machine/device. Use this skill whenever the user says 'resume', 'continue from last session', 'pick up where we left off', 'where were we', 'what was I working on', 'reload context', 'catch me up', 'continue on this device', or any indication that a new session needs to restore context from a previous one. Also trigger when the user starts a fresh chat (often on another computer) and references prior work or asks to continue something."
---

# Session Resume

This skill reconstructs context in a fresh session so it feels like a seamless continuation of the previous one — **including when the previous session ran on a different machine.** Maximum context recovery, minimum token spend.

## Shared Reference

The full methodology, edge cases, and native-systems table live in the `session-handoff` skill. Read `session-handoff/SKILL.md` if you need deeper context on the layered persistence design. This skill executes **Phase 2: RESUME** from that document.

## Cross-Device Principle (read first)

Cowork does **not** sync session state across devices. The previous session's auto-memory, transcript, and task sidebar may not exist on this machine. What always travels is the OneDrive-synced workspace. So **read the synced tier as the source of truth, and treat device-local stores as optional accelerators that are normally absent on a different device** — skip them silently when missing, without warning or re-deriving context the synced files already hold.

## Procedure

Execute these steps in order.

### Step 1: Identify the Project

1. Read `00.ABOUT/SESSION_HANDOFF.md` first — its fixed path means you can always find the latest resume point with no local state. It names the project and the latest RAM.
2. If that file is absent, fall back to `PROJECT_INDEX.md` for active projects.
3. If the user named a project, use that.
4. If multiple active projects exist (or SESSION_HANDOFF.md lists several), ask which one to resume.

### Step 2: Load the Context Stack

Read in this order. The **synced** layers are authoritative — read them on every resume. The **device-local** layers are accelerators — read them only if present, skip silently if not (the normal case on a different device).

**SYNCED — source of truth (always read):**

**Layer 1 — SESSION_HANDOFF.md** (`00.ABOUT`):
The stable cross-device pointer. Gives project, latest RAM path, task snapshot, open items, and recommended next action. This alone is usually enough to orient.

**Layer 2 — RAM** (`12.MEMORY_RAM`):
Read the latest RAM file named by Layer 1 / PROJECT_INDEX. The primary, complete context source — decisions, status, inputs, outputs, next actions.

**Layer 3 — PROJECT_INDEX.md** (`00.ABOUT`):
Confirms the RAM pointer and current-state summary; resolves any ambiguity about which project/RAM is current.

**Layer 4 — HDD** (`13.MEMORY_HDD`):
Only if RAM flags gaps or the user asks for deeper history.

**DEVICE-LOCAL — optional accelerators (read only if present):**

**Layer 5 — Auto-memory** (`.auto-memory/`):
`project_{slug}.md`, plus any `feedback_*.md` / `user_*.md` that inform how to work with this user. On a different device this is normally empty — that is **expected, not an error**. Do not block or warn; the synced tier already carries the resume content.

**Layer 6 — Prior session transcript** (`list_sessions` + `read_transcript`):
Pull the tail of the previous session (last 15-20 messages) to recover conversational tone and micro-context. Only meaningful on the same machine that ran the prior session. Read only if: the RAM has a Session Handoff Note, a recent matching session exists, and the user hasn't said to skip it. Absent on another device — skip gracefully.

### Step 3: Restore Task List

Rebuild the TodoWrite list from the `## Task Snapshot` in SESSION_HANDOFF.md (or RAM):
- Previously `completed` → `completed`
- Previously `in_progress` → `pending` (interrupted, not finished)
- Previously `pending` → `pending`

This restores the visible sidebar, which never traveled across devices on its own. If there's no snapshot, skip — the briefing will orient the user. Sync `TASKS.md` if it exists.

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

If you resumed on a **different device** (device-local layers were empty), say so briefly so the user knows the conversational tail wasn't available — e.g., "Resumed from the synced workspace (different machine), so I have the full project state but not the prior chat's exact wording." If a transcript *was* available, weave in any smoothing context ("You mentioned wanting to revisit X before moving on").

### Step 5: Confirm and Proceed

Wait for the user to confirm or redirect:
- Confirm → proceed normally
- Redirect → update working context
- Ask for more history → read HDD or older RAM versions

Once confirmed, the skill is done. Normal work resumes.

## Edge Cases

**No RAM exists for the project:**
Fall back to SESSION_HANDOFF.md → auto-memory → PROJECT_INDEX summary → ask the user. Create a RAM file as part of the resume so the next handoff has a proper starting point.

**Resuming on a different device than the wrap-up:**
A primary supported case. The device-local tier (auto-memory, transcripts) will be empty — expected. Proceed confidently from SESSION_HANDOFF.md + RAM; do not treat the missing local layers as an error.

**OneDrive hasn't finished syncing:**
If SESSION_HANDOFF.md is missing, older than the user expects, or its `updated` timestamp predates the work the user describes, the workspace may still be syncing on this device. Tell the user and offer to wait and re-read rather than rebuilding context from scratch. (Reading via the file tools fetches a cloud-only file on demand; if a bash command can't see it yet, it may not be materialized.)

**Multiple projects were active in the previous session:**
Present a choice if the user doesn't specify which to resume.

**RAM is stale (handoff happened many sessions ago):**
If the Session Handoff Note is dated more than a few days ago, flag it: "The last formal handoff was on {date}. Some context may have changed since then. Want me to scan for more recent activity before we proceed?"

## Model Recommendation

Use Sonnet if delegating to a subagent. The resume phase is procedural — a fixed sequence of reads and a structured briefing.

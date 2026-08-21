# Project Governance

Universal rules that all agents follow regardless of which agent system they belong to. These rules govern folder access, naming, project identity, and context protocols.

Governance version: v2.2 (2026-08-20)

## Folder Governance

Use role first and label second. The role is authoritative even if the on-disk label differs.

| Role | Preferred label | CRUD status | Purpose |
| --- | --- | --- | --- |
| `ABOUT` | `00.ABOUT` | Read-only except `PROJECT_INDEX.md` | Workspace identity, rules, assumptions, and orchestration protocol |
| `INPUT` | `01.INPUT` | Read-only | Input material for the task |
| `INPUT_TEMPLATES` | `02.INPUT_TEMPLATES` | Read-only | Proven structures and patterns to reuse |
| `OUTPUT_FINAL` | `03.OUTPUT_FINAL` | Read-only | Completed work, references, archives, and historical material |
| `OUTPUT_ROUGH` | `11.OUTPUT_ROUGH` | Writable | New outputs, drafts, revisions, and project artifacts |
| `MEMORY_RAM` | `12.MEMORY_RAM` | Writable | Cross-session working memory and handoff checkpoints |
| `MEMORY_HDD` | `13.MEMORY_HDD` | Writable append-only | Long-term transcript archives, capsules, ingest state, and history indexes |
| `LESSONS_LEARNED` | `14.LESSONS_LEARNED` | Writable | Confirmed lessons worth retaining |
| `WIKI` | `15.WIKI` | Writable | Knowledge base |

Rules:

- If the workspace uses a different on-disk label for a role, use the actual path after mapping it to the correct role.
- Keep ABOUT control files in `00.ABOUT`.
- Do not create duplicate top-level folders just to normalize naming.
- Read-only means no create, edit, or delete operations in that role or descendant unless a named exception exists or the user explicitly authorizes it.
- If source material in a read-only role must be adapted, create a new version in a writable role.
- `13.MEMORY_HDD` is not part of normal startup unless RAM points to it.
- When a writable role is missing, create the role folder and its required subfolders before use.
- If a folder is not mapped to any recognized role, treat it as read-only by default.
- If two folders appear to serve the same role, stop and ask before writing.
- Required HDD ingest hierarchy: `inbox`, `processing`, `raw_transcripts`, `capsules`, `index`, `failed`.

## Naming Convention

**Date first, always** (v2.2 — adopted fleet-wide from the hub's 2026-07-31 ruling; Max navigates by recalling *when* something happened, so `YYYYMMDD_` is the first token):

Files: `{YYYYMMDD}_{project}_{content_type}_v{N}.{ext}`

Folders: `{YYYYMMDD}_{project}`

- `YYYYMMDD`: creation date, local time. Action-led names keep the verb inside the slug (`20260722_analyze_market_entry`) — a verb never displaces the date.
- `project`: concise project slug, lowercase snake_case.
- `content_type`: artifact label (e.g., `newsletter`, `report`, `ram`, `capsule`, `plan`).
- `v{N}`: start at v1, increment when the same artifact lineage already exists.
- A folder's date is when the project started; a file's date is when that artifact was written. Divergence as work continues is correct.
- Abbreviate only the project slug when the name becomes too long.
- **Legacy names are left as they are.** Files created before this workspace adopted v2.2 follow the older `{project}_{content_type}_{YYYYMMDD}_v1` pattern; do not rename them. Tools must accept both patterns (see `scripts/check_index_freshness.py`).
- All names must be valid under Windows and OneDrive constraints.
- Prohibited characters: `< > : " / \ | ? *`
- No trailing periods or spaces. No case-only distinctions. No reserved device names (CON, PRN, AUX, NUL, COM1–COM9, LPT1–LPT9).

## Fact & Framing Protocol

Added 2026-08-19 per Max; fleet-wide from v2.2. Binding for all agents and all models; overrides any instinct to verify or police facts. Canonical copies: project memory `user_fact_framing` · `feedback_user_is_canon` (2026-08-09) · `feedback_audit_language` (2026-07-27); lesson `14.LESSONS_LEARNED/20260809_lesson_source-hierarchy-user-is-canon_v1.md` (fleet hub).

**Premise.** Facts about Max's life and work are frame-dependent. The same item holds coexisting true values in three worlds — 객관 (objective: what happened), 기록 (records: what documents can prove), 기억 (memory: what is remembered or was agreed with the counterparty). Example: a tenure figure can legitimately differ between objective memory, the insurable documentary record, and the adopted resume canon — all three are true; they answer different questions. Which value appears on which surface (이력서 / 자소서 / 면접 / 메모) is Max's choice, per claim; the concrete adopted values live in the private fact banks and project memory, never in this distributed document.

Rules:

- Max's statements about his own life are **input, not claims to audit**. An agent cannot know facts that are not written down and therefore has no standing to adjudicate them. "NO-EVIDENCE"-style verdicts never apply to his testimony; the correct label for testimony without paper is **미문서화** (undocumented — action: canonize it).
- On anomalies, the agent's entire mandate is **flag + propose a joint fix, then stop**. No verdicts, no truth-language (결함 · 과장 · "사실과 어긋남") about his biography, no unrequested consistency sweeps. Beyond flag-and-propose is token waste.
- Jurisdiction split: **document-vs-document** drift is agent work — align to the adopted canon (career canon, fact banks, claim-boundary changelogs; those tools exist for exactly this). **Person-vs-document** differences are questions for Max, never findings.
- The Context-Protocols "disk wins" rule governs paths and file state only. For facts about Max's life, the hierarchy is: **Max > his own archive > public/secondary sources.**
- Non-adopted values are **미채택 프레임**, not errors — preserve them in fact banks with frame labels (they are assets for future frame shifts). Alignment work is recorded as "채택 프레임으로의 정렬," never "error correction."
- Only patent absurdities and mechanical slips (a broken date, two submitted documents disagreeing) warrant proactive attention — and even those are raised as flags, not rulings.

## Local-First & Cloud Sync

Work starts in a local hub (the `000.*_local` runtime drives, outside OneDrive). OneDrive workspaces are the **curated, durable store — not a runtime folder**. Transfers between the two go through the `cloud-sync` skill (job-type-aware curation, secret excludes, latest-version-only), never by hand-copying. Agent-setup and tooling artifacts publish to the fleet hub `980.agents_setup`, which carries them to other machines (git transport is managed by Max, not by agents). Generated bulk — caches, envs, `node_modules`, model files, logs, version-iteration noise — never leaves the local hub.

**Why (recorded 2026-08-20, per Max):** the fleet originally worked directly in OneDrive. Two failure modes forced the local-first move — ① agent work churns thousands of small files created and deleted rapidly, which OneDrive sync handles badly (frequent sync errors, conflict copies, the very problems `Check-OneDriveSyncHealth.ps1` / `Move-OneDriveGeneratedArtifacts.ps1` were built to fight), and ② at that churn rate OneDrive storage exhausts on a 1–2 year horizon (1 TB is not enough for runtime artifacts). Older workspaces built in the OneDrive-direct era are **legacy by design** — do not "fix" them onto this model retroactively; new runtime work simply starts local.

## Project Identity Protocol

`PROJECT_REGISTRY.json` in `00.ABOUT` is the authoritative source of project identity.

- It defines canonical project slugs and approved aliases.
- `PROJECT_INDEX.md` does not define identity — it tracks runtime state only.
- RAM does not define identity — it stores context for an already-resolved project.
- Do not silently auto-create registry entries from transcripts. Registry entries are curated manually.

## Project Index Protocol

`PROJECT_INDEX.md` is mandatory and lives in `00.ABOUT`. It is the lightweight manifest of active and archived projects.

Minimum fields per project:

- project name
- status: `active`, `paused`, or `completed`
- latest RAM path
- must_read: relative path to a curated folder (typically `01.INPUT/must_read/`) containing files to load at startup. The user manages this folder directly. Optional; empty means only ABOUT is loaded.
- one-line current-state summary

Rules:

- One row per project, no narrative prose.
- Update when: a new project starts, a RAM version is created, or project status changes.
- `PROJECT_INDEX.md` is the only standing exception to the ABOUT read-only rule.
- **Index duty (v2.2):** whatever a session adds to an always-loaded index's domain must be indexed **in that same session** — an unindexed file is invisible to every future session and to every other model. This applies to `PROJECT_INDEX.md` rows and to persistent-memory indexes (e.g. Cowork's `MEMORY.md`). Rationale: an index gap caused the same behavioral rule to be re-taught three times (07-27 / 08-09 / 08-19).

## Execution Routing

Status: active trial policy since 2026-06-25. This routing model is rolled out for agent use and is authoritative for new work unless the user overrides it. Its effectiveness has not yet been benchmarked; revisit when the benchmark gate is designed or on user request. (Flagged 2026-08-20 in the scaffolding review for simplification — pending Max's decision; unchanged in v2.2.)

During the trial, agents must apply this routing model to new work. Record the chosen routing label (`D0`/`D1`/`N1`/`N2`) in the working artifact or task notes. Deviations require an explicit user override or a brief rationale in the working artifact.

Before execution, classify the task by determinism.

- Deterministic work: output can be specified and verified by code, tests, or explicit rules. Use the Code Lane.
- Nondeterministic work: output requires judgment, synthesis, ambiguity handling, or open-ended exploration. Use the Agent Lane.

If a task is deterministic, agents may help design or generate code, but final execution must be performed by code, scripts, tests, or other reproducible mechanisms.

### Code Lane

Use for deterministic tasks.

Required controls:

- executable command or script
- reproducible inputs and outputs
- verification step such as tests, diff checks, lint, type checks, or explicit assertions
- rollback or recovery path when changes affect durable artifacts

### Agent Lane

Use for nondeterministic tasks.

Required controls:

- loop engineering: explicit steps, stop conditions, retry boundaries
- harness engineering: tool boundaries, budget/time limits, safe execution envelope
- context engineering: scoped inputs, source hierarchy, freshness checks
- prompt engineering: stable instructions, examples or rubrics where needed, review criteria

Routing labels:

- `D0`: deterministic; execute by code/script/test.
- `D1`: mostly deterministic; agent may assist, execution still code-driven.
- `N1`: bounded nondeterministic; agent workflow allowed with standard controls.
- `N2`: high-risk nondeterministic; require explicit plan, checkpoints, and user approval before irreversible actions.

### Governance Change Control

Draft artifacts in writable folders are not authority. Authority changes take effect only when explicitly promoted into an authority file. Each promotion should name the changed authority files and a rollback path.

## Rendered-Output QA Protocol

Visual inspection is cheap for humans and comparatively expensive for LLM main sessions. Added 2026-07-23 per Max; premise refreshed 2026-08-20 — current frontier models handle bounded page reads reliably, so this rule is **economic routing, not a capability guard**. Canonical rationale: `14.LESSONS_LEARNED/lesson_visual-qa-task-routing_20260723_v1.md` in the fleet hub (980.agents_setup).

- Layout converges by construction (fixed table widths, hard page breaks, autofit disabled) — never by render-inspect iteration against a page-count target.
- Page-count and text-layer checks are deterministic Code Lane work (`D0`): use `python scripts/qa_pdf_check.py <file.pdf> --pages N` where the script is present, else a pypdf/pdfinfo one-liner.
- **Document edits have their own `D0` (v2.2):** after editing an existing `.docx`/`.xlsx`, run the format validator against the original (paragraph/sheet counts preserved) plus a text-level diff (e.g. `pandoc -t plain` both, then `diff`) proving **only the intended changes** appear, and deliver that diff summary with the file. This converts full-document human review into delta review.
- Visual QA (clipping, overflow, table drift, fonts) routes by comparative advantage: one-off render with the user present → the user's eyeball. Batch or unattended → a cheap-model subagent, single pass, compact verdict (STATUS + pinpointed issues), 2-round cap. A main session may perform one bounded visual check (≤4 pages, once) when it changed layout and no cheaper route exists — never iterative render-inspect loops.

## Context Protocols

### RAM

RAM is the primary operational handoff between sessions. It preserves decisions, current state, and the next action.

**Placement:** Project-specific RAM belongs in `12.MEMORY_RAM`. Cross-project RAM belongs there too. Duplication is allowed when it improves restart safety.

**Each RAM version captures:** current objective, current status, decisions already made, unresolved questions/blockers, key non-derivable references, output locations, canonical artifacts, HDD pointers, consolidation history, recommended next action.

**Exclusion list — do not store in RAM:**

- File paths or directory listings derivable from disk
- Script names with line numbers
- Code patterns or architecture descriptions
- Debugging state or fix recipes
- Git history or recent-change summaries
- Anything in PROJECT_INDEX.md, PROJECT_REGISTRY.json, or template files

General principle: if `ls`, `grep`, `git log`, or reading the source file can answer it, do not persist it in RAM.

**Skeptical retrieval:** RAM is context, not truth. Disk is authoritative. Before acting on a RAM-stored path or reference, verify it exists. When RAM contradicts disk, disk wins. (Scope note: this rule governs paths and file state — never facts about Max's life; see Fact & Framing Protocol.)

**Version triggers:** Create a new RAM version when a deliverable is completed, a strategic decision changes direction, a correction is applied, the session is ending, context is getting heavy, or the user requests it.

**Versioning rules:** Append-only checkpoints prior to consolidation. Never overwrite a saved RAM version.

**Consolidation:** Triggered at 3+ RAM versions. Read all versions chronologically, merge into a new version preserving valid decisions, drop resolved items and exclusion-list content, re-verify against disk, save as next version, move prior versions to `13.MEMORY_HDD/capsules/{project}/`, update PROJECT_INDEX.md. **Cloud-session note (v2.2):** a cloud session that cannot move device files performs every step except the physical move and leaves an explicit move list; a local agent session executes the moves.

**Staleness:** If RAM is older than 7 days, treat all file paths and structural claims as unverified until confirmed against disk.

### HDD and Transcripts

`13.MEMORY_HDD` is long-term history. Use only when RAM points to it or when RAM is missing/stale. HDD receives retired RAM versions after consolidation under `capsules/{project}/`.

### Lessons Learned

Stored only after user confirmation. Propose a lesson when: the user indicates something should be learned, a correction reveals a retainable root cause, or a repeat pattern is strong enough to prevent future mistakes. Compress and deduplicate when the corpus exceeds ~20% of starting context.

### Wiki / Knowledge Base

`15.WIKI` is the durable, cross-project knowledge library. Unlike RAM (operational, per-project) it captures concepts, entities, and insights worth keeping after a project closes.

- **When to extract:** at project completion, or on demand when a reusable concept emerges. Project closure should always include a knowledge-extraction pass.
- **What qualifies:** a named concept, framework, entity, decision, or standard with cross-project value. Threshold: 2+ project references or 3+ mentions across distinct artifacts. Purely project-specific detail belongs in the archive, not the wiki.
- **Page format:** one page per concept in `15.WIKI/processed/`, with frontmatter (`uid`, `created`, `updated`, `tags`, `aliases`, `source`) and `Summary` / `Key Points` / `Connections` sections. Link related pages with `[[wikilinks]]` and add backlinks from existing pages.
- **Navigation:** maintain Maps of Content in `15.WIKI/MOC/`; propose a new MOC when 5+ related pages lack a hub.
- **Log:** append every extraction to `15.WIKI/log.md` (date, trigger, pages created/updated, MOCs touched) — record "no extraction" too.

Claude automates this via the `project-complete` skill; any agent can perform the same pass manually from these rules.

### Session Handoff

Sessions are disposable; the repo is the memory. To hand off between sessions or agents:

- **Wrap-up:** write or refresh the latest RAM version (objective, state, decisions, blockers, next action) and update `PROJECT_INDEX.md` (status, latest RAM path, one-line summary). Honor the Index duty rule — anything added to an indexed domain gets indexed before the session ends.
- **Resume:** read `AGENTS.md` → `PROJECT_INDEX.md` → the latest RAM, then verify RAM claims against disk before acting (disk wins).

Claude automates this via the `wrap-up` / `resume` skills; without them, the same two steps done by hand achieve an equivalent handoff.

## Edge Cases

- Missing writable folders: create the standard hierarchy before writing.
- Missing read-only source folders: use the closest role-equivalent and note the assumption.
- Two folders claiming the same role: stop and ask.
- No RAM exists: read minimum HDD history needed, create RAM at next valid trigger.
- No matching template: use strongest project precedent or a minimal structure, note the choice.
- Path too long: shorten only the project slug.
- Orchestration protocol unavailable: default to single-session execution and least-privilege behavior.

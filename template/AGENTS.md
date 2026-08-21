# AGENTS.md

**{project_name}** — {one-line description of what this project is and why it exists}. Status: {active / paused / completed}.

## Folders

| Path | Access | Purpose |
| --- | --- | --- |
| `00.ABOUT/` | read-only¹ | Governance, project manifest, protocols |
| `01.INPUT/` (`must_read/`) | read-only | Source material; curated startup context |
| `02.INPUT_TEMPLATES/` | read-only | Reusable structures |
| `03.OUTPUT_FINAL/` | read-only | Completed deliverables |
| `11.OUTPUT_ROUGH/` | writable | Drafts, in-progress work, active plans |
| `12.MEMORY_RAM/` | writable | Cross-session working memory |
| `13.MEMORY_HDD/` | append-only | Long-term history, transcripts |
| `14.LESSONS_LEARNED/` | writable | Confirmed lessons |
| `15.WIKI/` | writable | Knowledge base |

¹ `PROJECT_INDEX.md` is the standing exception.

## Hard rules

1. The access column above is authoritative. Unmapped folders are read-only by default; if two folders claim the same role, stop and ask.
2. New files: `{project}_{content_type}_{YYYYMMDD}_v1.{ext}`, valid under Windows/OneDrive constraints.
3. Repository knowledge is the system of record — push context into files, not chat threads.
4. Route before you execute: classify new work as deterministic (Code Lane) or nondeterministic (Agent Lane) per `Execution Routing` in `PROJECT_GOVERNANCE.md`.

## Deeper documentation (load on demand)

| Doc | What |
| --- | --- |
| `00.ABOUT/PROJECT_INDEX.md` | Runtime state: status, latest RAM path, `must_read` pointer — read at startup |
| `00.ABOUT/PROJECT_GOVERNANCE.md` | Full rules: naming, identity, execution routing, RAM/HDD/wiki/handoff protocols |
| `00.ABOUT/CLAUDE.md` | Claude startup pointer |
| `00.ABOUT/orchestration_protocol.md` | Delegation, worker contracts, synthesis |
| `00.ABOUT/PROJECT_REGISTRY.json` | Canonical project slugs and aliases |

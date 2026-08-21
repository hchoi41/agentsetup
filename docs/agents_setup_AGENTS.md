# AGENTS.md

**agents_setup** — central hub for the agent-setup system: template development (`install_v5_clean/`), fleet distribution, global instruction management, and custom skill development. Status: active.

## Folders

| Path | Access | Purpose |
| --- | --- | --- |
| `00.ABOUT/` | read-only¹ | Governance, manifest, protocols, fleet registry |
| `01.INPUT/` (`must_read/`) | read-only | Source material; curated startup context |
| `02.INPUT_TEMPLATES/` | read-only | Reusable structures |
| `03.OUTPUT_FINAL/` | read-only | Completed deliverables, archives |
| `11.OUTPUT_ROUGH/` | writable | Drafts; skill sources under development (`dig/`, `plan/`) |
| `12.MEMORY_RAM/` | writable | Cross-session working memory |
| `13.MEMORY_HDD/` | append-only | Long-term history, transcripts |
| `14.LESSONS_LEARNED/` | writable | Confirmed lessons |
| `15.WIKI/` | writable | Knowledge base |
| `install_v5_clean/` | writable | Canonical project template for new deployments |
| `80.research/`, `best practices/` | read-only | Agent research; skill-authoring guides |
| `90.archive/` | ignore | Superseded generations, retired scaffolds (git-ignored) |
| `<skill>/`, `*.skill` | writable | Unpacked skill sources at root; packaged bundles |

¹ `PROJECT_INDEX.md` and `DEPLOYMENT_REGISTRY.json` are the standing exceptions.

## Hard rules

1. The access column above is authoritative. Unmapped folders are read-only by default; if two folders claim the same role, stop and ask.
2. New files: `{project}_{content_type}_{YYYYMMDD}_v1.{ext}`, valid under Windows/OneDrive constraints.
3. Repository knowledge is the system of record — push context into files, not chat threads.
4. Route before you execute: classify new work as deterministic (Code Lane) or nondeterministic (Agent Lane) per `Execution Routing` in `PROJECT_GOVERNANCE.md`.
5. Skills: the **unpacked source is canonical**; `*.skill` packages and `.claude/skills/` copies are built from it. Edit the source, then repackage — never edit a package or installed copy. Non-Claude agents ignore skills and follow `PROJECT_GOVERNANCE.md` instead.

## Fleet distribution

`install_v5_clean/` is the source of truth for deployments. `00.ABOUT/DEPLOYMENT_REGISTRY.json` (hub-only) tracks the fleet: deployment paths, governance version, drift state, and the distribution protocol (which files ship verbatim vs. merge). Consult it before distributing or editing distributed files.

## Deeper documentation (load on demand)

| Doc | What |
| --- | --- |
| `00.ABOUT/PROJECT_INDEX.md` | Runtime state: status, latest RAM path, `must_read` pointer — read at startup |
| `00.ABOUT/PROJECT_GOVERNANCE.md` | Full rules: naming, identity, execution routing, RAM/HDD/wiki protocols |
| `00.ABOUT/CLAUDE.md` | Claude startup pointer |
| `00.ABOUT/orchestration_protocol.md` | Delegation, worker contracts, synthesis |
| `00.ABOUT/PROJECT_REGISTRY.json` | Canonical project slugs and aliases |
| `00.ABOUT/DEPLOYMENT_REGISTRY.json` | Fleet inventory + distribution protocol |
| `agents_setup_global_instructions_*.md` | Standalone global fallback when no project folder is mounted |

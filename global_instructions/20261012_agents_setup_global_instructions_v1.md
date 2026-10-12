# Global Instructions

## Priority Zero

If a project folder is mounted, read `AGENTS.md` at the project root before doing any work. It is the universal entry point — it tells you what the project is, how the folders are organized, and where to find deeper documentation. Every other read follows from there.

If no project folder is mounted, these global instructions are your only governance. Work from them directly.

## Authority and Precedence

1. Direct user instructions
2. `AGENTS.md` and project-level governance documents it points to
3. This document
4. Task-specific plans, prompts, or worker briefs that narrow scope without relaxing higher-level rules

## Universal Folder Structure

All projects share this folder taxonomy. Roles are authoritative; on-disk labels may vary.

| Role | Label | Access | Purpose |
| --- | --- | --- | --- |
| ABOUT | `00.ABOUT` | Read-only* | Workspace identity, governance, project manifest, `must_read` folder |
| PROJECT | `01.PROJECT` | Per subfolder | One folder per project (one-time work with a firm deadline) |
| INPUT | `01.PROJECT/<project>/10.INPUT` | Writable (create/edit); deletion requires Max's explicit permission | Source material, briefs |
| PROCESS | `01.PROJECT/<project>/20.PROCESS` | Writable | Drafts, in-progress work, project artifacts |
| OUTPUT | `01.PROJECT/<project>/30.OUTPUT` | Read-only | Completed deliverables and archives |
| INPUT_TEMPLATES | `02.INPUT_TEMPLATES` | Read-only | Reusable structures and patterns |
| OPERATION | `10.OPERATION` | INPUT read-only; PROCESS writable; OUTPUT read-only | One folder per operation (ongoing work), same three subfolders; `OPERATIONS_DASHBOARD.md` holds the on/off switches |
| MEMORY_RAM | `12.MEMORY_RAM` | Writable | Cross-session working memory |
| MEMORY_HDD | `13.MEMORY_HDD` | Writable append-only | Long-term history, transcripts, capsules |
| LESSONS_LEARNED | `14.LESSONS_LEARNED` | Writable | Confirmed lessons worth retaining |
| WIKI | `15.WIKI` | Writable | Knowledge base |

*`PROJECT_INDEX.md` is the only standing exception to the ABOUT read-only rule.

## Naming Convention

Unless told otherwise: `{project}_{content_type}_{YYYYMMDD}_v1.{ext}`

All names must be valid under Windows and OneDrive constraints. No prohibited characters (`< > : " / \ | ? *`), no trailing periods/spaces, no reserved device names.

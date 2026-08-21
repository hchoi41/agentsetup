# Claude Cowork HDD Ingestion Protocol

Use this document only for transcript ingestion, RAM repair, or HDD maintenance. Normal task startup should not read it.

## Purpose

This protocol defines the lightweight v1 ingest flow for Claude Cowork transcript exports so new chats can start from RAM instead of raw history.

## Required Layout

Create this writable hierarchy first if any part of it is missing.

- `13.MEMORY_HDD/inbox/`
- `13.MEMORY_HDD/processing/`
- `13.MEMORY_HDD/raw_transcripts/<project_slug|_unassigned>/`
- `13.MEMORY_HDD/capsules/<project_slug|_unassigned>/`
- `13.MEMORY_HDD/index/ingest_manifest.jsonl`
- `13.MEMORY_HDD/failed/`

## Ingest Rules

1. Compute `content_hash` as SHA-256 of the raw export zip bytes.
2. Read the transcript identifier from the export metadata when available.
3. Before creating a capsule or RAM file, check `13.MEMORY_HDD/index/ingest_manifest.jsonl` for the same `transcript_id + content_hash`.
4. If the pair already exists, skip the export and create no new capsule or RAM checkpoint.
5. Use the HDD-managed state flow: `inbox -> processing -> raw_transcripts` on success and `inbox -> processing -> failed` on failure.

## Project Mapping Rules

Gather project signals from:

- the export title
- selected workspace folders
- referenced file paths inside the transcript

Rules:

- `PROJECT_REGISTRY.json` is the identity authority when it exists and contains projects.
- Keep `PROJECT_REGISTRY.json` in `00.ABOUT` beside `PROJECT_INDEX.md`.
- Match registry `root_paths` first, then registry `aliases`.
- Ignore generic attachment mount roots such as `/mnt/uploads` when determining the project.
- Use `title_keywords` only as weak supporting evidence. Title-only resolution is allowed only when exactly one registry project matches at least 2 distinct keywords and no stronger evidence conflicts.
- If the registry exists and has projects, do not use free-form heuristic identity resolution.
- Use legacy heuristic fallback only when the registry file is missing or empty.
- Update project RAM only when the available signals resolve to exactly one project.
- If signals conflict or no single project is clear, store the export under `_unassigned`, create a capsule there, and do not update project RAM.

## Capsule and RAM Rules

Capsules are historical summaries only. Each capsule must contain:

- `project`
- `session_date`
- `objective`
- `durable_decisions`
- `artifacts_referenced_or_produced`
- `recommended_ram_impact`

RAM updates stay append-only:

- Create at most one new RAM checkpoint per unambiguous transcript export.
- If a project has no RAM yet, create its first RAM checkpoint from the capsule.
- If a RAM update is later judged wrong, correct it by creating a newer RAM version.
- Never rewrite or delete older RAM files during repair.

## Retrieval Guardrails

- Normal startup is `PROJECT_INDEX.md -> latest RAM`.
- HDD is consulted only from RAM pointers or when RAM is missing or clearly insufficient.
- Old archived file versions stay out of normal retrieval.
- When RAM is missing or broken, fallback order is `capsules first`, `old archived files second`.

## Manifest Fields

`13.MEMORY_HDD/index/ingest_manifest.jsonl` uses:

- `transcript_id`
- `content_hash`
- `project_slug`
- `raw_path`
- `capsule_path`
- `ram_path`
- `ingested_at`

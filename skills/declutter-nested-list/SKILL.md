---
name: declutter-nested-list
description: "Declutter, deduplicate, classify, and reorganize deeply nested outlines while preserving source meaning, hierarchy, links, tags, dates, and traceability. Use when the user says DeclutterNestedList, asks to clean up or reorganize a nested list, or provides a WorkFlowy export, OPML, Markdown outline, HTML list, indented text, or similar hierarchy containing duplicate branches, mixed tasks and notes, ambiguous placement, stale items, or excessive nesting."
---

# DeclutterNestedList

Turn a cluttered hierarchy into a usable outline without silently losing or inventing content. Treat decluttering as controlled information architecture, not copyediting.

## Operating contract

- Read the applicable workspace governance before acting.
- Preserve source files. Write derivatives only to an authorized writable location.
- Use the original hierarchy as evidence; do not infer that an item is complete, stale, private, or disposable merely from wording or age.
- Preserve exact wording where meaning is uncertain. Put uncertain items in a review queue instead of deleting or aggressively rewriting them.
- Preserve URLs, tags, dates, IDs needed for round-trip traceability, and meaningful parent-child relationships.
- Distinguish exact duplication from conceptual similarity. Remove exact duplicated branches from the derivative; merge similar ideas only when the user approves the semantic merge or the equivalence is unambiguous.
- Keep facts, user-authored ideas, decisions, tasks, and agent recommendations visibly distinct.

## Choose the mode

Infer the least-destructive mode that satisfies the request:

1. **Audit** — inventory the hierarchy and report clutter without producing a rewritten outline.
2. **Proposal** — produce a proposed information architecture and move map, leaving the source unchanged.
3. **Apply** — produce the decluttered derivative plus its mapping and QA report. Use this when the user explicitly asks to clean, organize, transform, or create the result.

If the requested destination or output format is not specified, use the workspace's writable draft area and preserve the source format when practical. Otherwise emit Markdown because it exposes hierarchy clearly and is easy to review.

## Workflow

### 1. Establish the source boundary

List the supplied folder before reading files. Identify:

- source format and encoding;
- file count, size, and modification date;
- total nodes, top-level roots, maximum depth, links, tags, and date tokens;
- source hash when operating on a file;
- malformed markup, decoding problems, or export artifacts;
- repeated blocks or IDs that suggest duplicated exports.

For HTML or OPML, parse structure rather than stripping tags with a broad regular expression. When the source contains repeated full blocks with different IDs, compare normalized label sequences and child structure before declaring them duplicates.

### 2. Build a lossless inventory

Assign each source node a stable inventory key. Prefer the source ID; otherwise derive a path key from sibling ordinals. Record:

- original text;
- original parent and depth;
- links, tags, and dates;
- normalized text used only for comparison;
- detected type;
- proposed destination;
- action and rationale.

Use these item types as a starting vocabulary, adapting only when the material requires it:

- `task` — an action with a deliverable or next step;
- `decision` — a commitment, rule, or chosen direction;
- `project` — a multi-step outcome or active workstream;
- `reference` — a link, path, person, book, media item, or factual resource;
- `framework` — a reusable method, checklist, taxonomy, or analytical model;
- `idea` — an undeveloped possibility or question;
- `observation` — an experience, diagnosis, or factual note;
- `personal` — family, health, journal, or other personal-life material;
- `archive-candidate` — apparently inactive material that still needs confirmation before archival;
- `unclear` — incomplete or context-dependent material that must remain visible.

Classification changes placement, not meaning. Do not force every item into a task system.

### 3. Diagnose clutter

Flag each issue explicitly:

- exact duplicate branch;
- repeated or near-duplicate idea;
- mixed semantic types under one parent;
- orphan or incomplete fragment;
- excessive nesting with no added meaning;
- heading used as an item or item used as a heading;
- vague task without next action;
- dated commitment with unknown current status;
- reference mixed into an action list;
- likely sensitive or personal content mixed into a shareable outline.

Do not label an item obsolete solely because its date is in the past. Mark its status `unknown` unless the source supplies completion evidence.

### 4. Design the target hierarchy

Prefer a shallow, purpose-based structure. A useful default is:

```text
Active
  Projects
  Next actions
  Decisions and rules
Incubating
  Ideas and questions
Knowledge
  Frameworks and checklists
  References and media
Personal
Archive candidates
Needs review
```

Treat this as a scaffold, not a mandatory taxonomy. Retain domain-specific structures that already work, such as a complete industry-analysis framework. Split mixed roots instead of flattening valuable subtrees.

Use these placement rules:

- Organize actions by outcome or project, not merely by date.
- Keep short-, mid-, and long-term horizons only when the horizon changes a decision.
- Put reusable analytical structures under `Knowledge/Frameworks`.
- Keep file paths and URLs with the item they support, or under a linked reference branch.
- Separate personal notes from career or work material when the output may be shared.
- Route incomplete fragments and disputed merges to `Needs review` with their original context.
- Limit ordinary depth to three meaningful levels when possible; exceed it when hierarchy carries real analytical meaning.

### 5. Apply controlled transformations

Use this order:

1. Remove proven exact duplicate blocks from the derivative.
2. Normalize structurally empty wrappers and redundant headings.
3. Move intact branches before splitting them.
4. Split mixed branches by semantic type.
5. Collapse single-child chains only when the intermediate label adds no meaning.
6. Consolidate unambiguous duplicate items and retain all unique metadata.
7. Send ambiguous fragments, status questions, and possible semantic duplicates to `Needs review`.

Do not silently:

- mark tasks completed;
- turn ideas into commitments;
- convert observations into facts beyond the author's claim;
- rewrite quoted or sensitive personal wording;
- discard links, tags, dates, or source paths;
- merge items that merely share keywords;
- overwrite the original export.

### 6. Produce reviewable outputs

For Proposal or Apply mode, provide:

1. **Decluttered outline** — the proposed or applied hierarchy.
2. **Move map** — one row per source node or moved branch with `source_key`, `original_path`, `new_path`, `action`, `type`, and `reason`.
3. **Review queue** — ambiguity requiring a user decision, stated as focused questions.
4. **QA summary** — counts before and after, duplicates removed, items merged, items needing review, maximum depth, link/tag/date preservation, and source hash.

If workspace rules require provenance sidecars for a material artifact, create them and run the required provenance check.

## Validation gates

Do not call the result complete until all applicable checks pass:

- Every unique source node maps to exactly one destination, an explicitly documented merge, or the review queue.
- No unique link, tag, or date is lost.
- Removed nodes are proven duplicates or empty structural wrappers.
- Before/after counts reconcile:

  `unique source nodes = retained nodes + merged-away duplicates + intentionally excluded wrappers`

- The source hash and modification timestamp remain unchanged.
- The derivative parses or renders in its target format.
- Personal material has not been placed into a broadly shareable work section without an explicit warning.
- All status claims are sourced; unknown status remains unknown.

Report completed transformations, proposed changes, assumptions, and unresolved questions separately.

## Compact example

Source:

```text
Ideas
  Apply to roles
  Book: Example
Ideas
  Apply to roles
```

Derivative:

```text
Active
  Next actions
    Apply to roles [status: unknown]
Knowledge
  References and media
    Book: Example
```

The move map must show that the second `Ideas` branch and its child were exact duplicates, rather than making their disappearance implicit.

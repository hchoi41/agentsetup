---
name: minto-pass
description: Audit application and business writing for Minto pyramid order, dual-audience readability (HR + hiring team), and numbers with context; report by location and stage rewrites as diff cards.
---

## When to use
The user wants business or job-application writing (resume, cover letter, 경력기술서, 자소서, memo, brief) checked against three standards: Minto pyramid structure, readability for a mixed HR + hiring-team audience across industries, and numbers that carry context. Trigger phrases: "minto pass", "pyramid check", "is this exec-ready", "lead with the answer", "too much jargon", "give the numbers context", "make it strategic and parsimonious", or `/minto-pass`.

Works in any language; rewrites stay in the document's language.

## Inputs
- `target` — the user's current selection. If nothing is selected (cursor only), ask with `ask_user_question` which section to audit, listing the document's section titles. Do not default to the whole document.
- `jd` (optional) — an attached or uploaded job description. If present, read it (`code_execution` for uploaded files) to learn which terms and acronyms the reader already uses.

## Goal
Every paragraph and bullet in the target opens with its result or conclusion, uses no undefined acronym or industry-only term, carries no padding, and gives every figure a brief comparison — with each fix staged as a `propose_doc_edits` card and nothing changed until the user clicks Apply.

## Steps

### 1. Resolve scope
Use the highlighted text if there is one. Otherwise ask with `ask_user_question` which section to audit. Never scan the whole document unasked.
**Done when:** you know the exact paragraph range.
**Produces:** a heading or paragraph range for step 2.

### 2. Read the target and the JD
Call `read_doc_section` on the range. If a JD is attached, read it and list (a) terms and acronyms it uses natively — do not flag these as jargon — and (b) the metrics it cares about, which guide which numbers most need context. Skip table cells that hold pure data (names, dates, labels).
**Done when:** the target text is in context and the JD vocabulary list exists (or is noted as absent).
**Produces:** the JD vocabulary list used in step 4.

### 3. Pyramid check
For each paragraph or bullet, test:
- Does the first clause state the result, decision, or conclusion? If the outcome appears only at the end (or not at all), draft a reordered version: outcome → action → method.
- Are grouped items in one sentence or list non-overlapping and ordered by a visible logic (importance, time, or structure)? Flag overlaps and random order.
- Does the summary or opening paragraph state the single main point before the supporting facts?
**Done when:** every unit is tagged "pyramid-ok" or has a reordered draft.

### 4. Audience check
Two failure modes, both flagged:
- **Too technical:** acronyms not spelled out on first use (rule: formal name first, abbreviation in parentheses, abbreviation thereafter — only if the term repeats); industry-only jargon a general business reader would not know; strings of tool or system names with no stated purpose. Terms on the JD vocabulary list are exempt.
- **Too verbose:** explanations, background, or enumerations that add words without adding a decision-relevant fact. Prefer a higher-level term over a list.
Draft the plain, professional replacement — the standard is "readable by an HR screener and by an executive of a large multinational in one pass."
**Done when:** every flagged phrase has a replacement drafted or is consciously kept (note why).
**Rule:** Spell out first use, then abbreviate. Do not expand universally known tool names (SQL, Excel, Power BI).

### 5. Number-context check
For every figure (%, currency, count, duration, rank), check whether the sentence says what it means: compared with a target, prior period, rival, benchmark, or rank. If the comparison exists in the document, draft the sentence with it. If no comparison is available, add the figure to a "bare numbers" list.
**Done when:** every figure is either contextualized in a draft or on the bare-numbers list.
**Rule:** Do not invent a comparison. For bare numbers, ask the user with `ask_user_question` (one batched call listing each figure) what the baseline or benchmark is before drafting; if the user has none, leave the figure as is.

### 6. Report, then stage cards
Reply in chat, bullets with a bold location label, grouped under three short headers — **Pyramid**, **Audience**, **Numbers** — one bullet per hit:
- **[Section title](<citation:heading:Section title>)** — "original" → "rewrite" — one-line reason.
Then stage every rewrite via `propose_doc_edits` (minimal old_text/new_text span; reasoning names which of the three checks drove it). Group into separate calls by check if there are more than ~5 cards. Do not write rewrites directly with `edit_doc_text`.
**Done when:** the chat report lists every hit and every rewrite has a pending card; close with one line: "Proposed N edits — review above."
**Rule:** Proposals first, approval before any change. The document is untouched until the user clicks Apply.

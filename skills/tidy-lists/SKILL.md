---
name: tidy-lists
description: Standardize delimiter usage (commas, colons, semicolons, middle dots, slashes, etc.) for consistency within the document and with industry/academic convention, and compress inline enumerations by omitting minor items or abstracting to a higher-level term.
---

## When to use
User asks to standardize delimiters/punctuation separators, make list punctuation consistent, "tidy the lists", "stop listing everything", "compress the enumerations", "fix the commas/colons/semicolons/middle dots", or invokes `/tidy-lists`. Works in any language (handles Korean `·`, `,`, `/`, `~` alongside English conventions).

## Inputs
- `target` — scope to operate on (default: the current selection; entire document if nothing is selected or the whole document is selected)

## Goal
Every delimiter role in the target (item separator, range, pairing, label introducer, clause separator) uses exactly one consistent mark that matches the accepted convention for the document's language, and no inline enumeration lists more items than the sentence needs.

## Steps

### 1. Inventory delimiter usage
Read the target (`<doc_state>` full text, or `read_doc_section` for long docs). Build a table of delimiter *roles* → marks actually used, e.g. "item separator within a noun phrase: `·` ×6, `,` ×3, `/` ×1"; "range: `~` ×2, `-` ×1"; "label introducer: `:` ×4". Note also the external convention for the document's language (e.g. Korean: `·` for compact noun-phrase lists, `,` for clause-level lists, `~` for numeric ranges, `:` after list-item labels; English/academic: serial comma policy consistent, en dash for ranges, semicolon only between independent clauses or list items containing commas).
**Done when:** every role has a count per mark and a stated external convention.
**Produces:** the role → marks table used in Steps 2–3.

### 2. Resolve conflicts with the user
For each role where the document's dominant mark differs from the external convention (or the document is split), call `ask_user_question` once — one question per conflicting role, batched in a single call — offering: the external-convention mark, the document-majority mark. Do not guess.
**Rule:** Always ask when document usage and external convention conflict; never silently pick one.
**Done when:** every role has a single chosen mark.

### 3. Apply delimiter normalization directly
For each deviating instance, apply the chosen mark with `edit_doc_text` using the smallest unique span (the item plus a neighbor word — not the whole sentence). Never replace across `[field]`, `[fn]`, or `[img]` markers; edit around them. Skip anything inside quoted source text, code, URLs, or numeric formats (thousands separators, decimals, dates).
**Done when:** a re-read of the target shows one mark per role; report `N delimiter fixes across M paragraphs`.

### 4. Find over-long inline enumerations
Scan prose sentences (not formal list paragraphs) for runs of 3+ coordinated items. For each, decide whether the full list is load-bearing. It is load-bearing — leave it intact — if it is any of:
- a formal bulleted/numbered list paragraph (`[ListParagraph]`, `[•]`, `[1.]` markers)
- a definition, taxonomy, or framework enumeration (e.g. "the four elements are…")
- cited data, figures, dates, or named sources
Otherwise it is a candidate for compression.
**Done when:** each candidate is tagged with its compression strategy.

### 5. Draft compressions and stage as review cards
For each candidate, prefer strategies in this order:
1. **Abstract** — replace the run with a single higher-level term that covers it ("댓글, 수정 기록, 권한" → "협업 히스토리"; "Slack, Teams, email" → "messaging channels").
2. **Omit** — keep the 1–2 most important items and drop the rest, with no trailing marker.
3. **Trail** — keep the key items and end with "등"/"etc." **only** when the reader must know the list is non-exhaustive and no abstraction exists. Cap: at most one "등/etc." per ~500 words; if you'd exceed that, go back to strategies 1–2.
Stage every compression via `propose_doc_edits` (minimal old_text/new_text span, reasoning names the strategy used). Do not write compressions directly.
**Rule:** Compression changes meaning — always review cards, never `edit_doc_text`.
**Done when:** all candidates are proposed; chat reply is one line: `Proposed N list compressions — review above.`

### 6. Verify
Re-read the edited paragraphs. Confirm no delimiter reverted, no field/footnote marker was lost, and formal lists still render their markers (`verify_doc`).
**Done when:** the re-read matches the role table from Step 2 and `verify_doc` shows unchanged list structure.

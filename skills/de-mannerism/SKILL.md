---
name: de-mannerism
description: Scan a document for mannered, metaphorical, or performative phrasing and report literal replacements by location — audit only, nothing changes until the user asks.
---

## When to use
The user wants prose checked for mannerism — metaphor and flourish standing in for direct statement ("a dial worth turning" for "a parameter worth varying"; "earns its keep" for "still matters"). Trigger phrases: "de-mannerism", "check for mannered prose", "find the metaphors", "is this too flowery", "say what you mean pass", "plain prose audit", or when the user pastes a rule about preferring literal phrasing.

Works in any language. Replacements stay in the document's language.

## Inputs
- `target` — the user's current selection. If there is no selection (cursor only), ask which section to audit with `ask_user_question`, listing the document's section titles as options. Do not default to the whole document.

## Goal
A report in chat listing every mannered phrase in the target with its location, the original wording, a literal replacement, and a one-line reason — or a plain statement that none were found. The document is untouched.

## Steps

### 1. Resolve scope
Use the highlighted text if there is one. Otherwise ask with `ask_user_question` which section to audit. Never scan the whole document unasked.
**Done when:** you know the exact paragraph range.
**Produces:** a heading or paragraph range for step 2.

### 2. Read the target
Call `read_doc_section` on the range from step 1. Skip table cells that hold pure data (names, dates, numbers, labels).
**Done when:** you have the full text of the target in context.

### 3. Flag mannered phrases
Test each phrase: does a literal phrase exist that says the same thing? If yes and the writer chose a figurative one, flag it. Do **not** flag:
- Idioms with no natural literal equivalent in that language.
- Domain terms the audience or job posting itself uses (e.g., a product or program name, an industry acronym). Note these separately as "jargon, keep if the reader uses it" only if borderline.
- Ordinary functional verbs in that language even if etymologically figurative (Korean 연결하다 in business usage, English "address," "drive" in fixed collocations) — flag only when a plainly more concrete verb is available.
**Done when:** every candidate has a literal replacement drafted or has been consciously excluded.

### 4. Report
Reply in chat, bullets with a bold location label, one bullet per hit:
- **[Section title](<citation:heading:Section title>)** — "original phrase" → "literal replacement" — one-line reason.
If nothing qualifies, say so in one sentence and name the one or two closest borderline cases with why they were kept. Do not pad the list.
**Done when:** the user can act on each line without re-reading the document.

### 5. Stop
Do not call `edit_doc_text`, `propose_doc_edits`, or `update_instructions`. Close with one line: offer to stage the approved items as diff cards, and (if the user pasted a general style rule) offer to save it as a standing instruction — but only do either when asked.
**Rule:** Proposals first, approval before any change. The user corrected this in the originating session after an instruction was saved without approval.

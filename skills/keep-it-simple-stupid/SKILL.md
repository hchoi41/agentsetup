---
name: keep-it-simple-stupid
description: Make an output do one thing for one reader. Fix its single purpose, keep in the body only what that purpose needs, move what accuracy requires to an appendix, cut the rest, and remove the notes scattered through the body. Works on any output: prose, code, spreadsheet, HTML, diagram, design. Use when the user says "apply KISS rule", "kiss", "keep it simple", "한 번에 하나씩", "too many things at once", "too many notes", "주석이 너무 많다", "simplify this", or invokes /keep-it-simple-stupid or /kiss. Alias: kiss.
---

# keep-it-simple-stupid

One output, one purpose, one reader. Putting in more is the author's convenience; the output exists for the reader's understanding.

## Inputs

- `target`: the output to simplify, or the task about to be done. Default: the output just produced or under way.
- `reader`: who will use it. Default: read it from the request.
- `purpose`: what the reader must be able to do afterwards. Default: read it from the request; if it cannot be read, ask (step 1).

## Procedure

1. **Write the purpose in one sentence**: "After this, <reader> can <decide / do / know> ___." When the request and its files do not let you write it, ask the author, the person who first gave the task. Ask one question at a time, the most decisive first, with two or three concrete options. Keep asking until the sentence can be written. Never cover the doubt by producing an output that serves several purposes.
2. **One purpose per output.** If the sentence needs "and", there are two outputs: make the one asked for, and name the other in one line at the end.
3. **Sort every element** (sentence, comment, cell, box, line, colour) into four piles:

   | pile | test | where it goes |
   |---|---|---|
   | body | the reader needs it to do what the purpose sentence says | the one page |
   | appendix | the detail matters (accuracy or the record needs it: history, derivation, full data, options not taken, a caveat that changes nothing the reader does) | an appendix at the end of the output |
   | cut | a duplicate, or a mark or detail that carries no meaning | removed |
   | ask | it has substance, the body does not need it, and you would rather drop it than append it | the author decides: ask whether it may be left out; until they answer it stays in the appendix |

4. **Build the body as one page** in the order the reader needs it (see the table below for what one page means per form). The body must work with the appendix removed.
5. **Empty the body of notes.** Each parenthesis, caveat, callout, comment or footnote is one of three things: content (it changes what the reader does: state it once, in its place, as body), appendix, or cut. The body may carry one pointer per appendix ("Details: Appendix A").
6. **Give every mark one meaning.** Position, line, colour, weight, symbol and punctuation form a grammar the reader decodes. A mark whose meaning you cannot state is removed; the same mark means the same thing everywhere. Delimiters: `/` is or; `,` is and (`&` as fallback); a middle dot that could be either is replaced.
7. **Check.** Read the body alone: can the reader do what the purpose sentence says? Are the parts MECE and in a logical order? If not, the thinking is unfinished; return to step 3, do not add notes.
8. **Report in at most five lines**: the purpose sentence, what moved to the appendix, what was cut, what the author is asked to decide.

## What one page means

| output | one page is | scattered notes look like | the appendix is |
|---|---|---|---|
| document, email, chat reply | one page or one screen; one message, one ask | parentheses, "note that", hedges, footnotes, asides | a closing appendix |
| code | one change, one purpose; one function, one job | comments that narrate the code, TODOs, commented-out code, branches for cases nobody asked for | the commit or PR description, a design note |
| spreadsheet | a first sheet that answers the one question | cell comments, colours with no key, "notes" columns | separate sheets for data, assumptions, log |
| HTML page, dashboard | one screen with one message or one action | tooltips, badges, banners, fine print | a details page, or one collapsed section at the end |
| diagram | one sheet, one level, one question | callouts, note boxes, a label on every edge | the sheet one level down, or a notes sheet |
| design file | one frame per state or flow | sticky notes on the canvas | a separate annotations page or spec |

## Rules

- **Nothing of substance is dropped without the author.** A detail that matters goes to the appendix at the end of the output, or the author agrees it may be left out. Only duplicates and meaningless detail are cut without asking.
- **The appendix is part of the output**, at its end. Use a separate file only when the form has no end to append to, or the author asks for one.
- **An existing file is never overwritten.** Write the simplified version beside it as a new version.
- **Do one thing per turn as well.** Deliver what was asked. Extra options, variants and unrequested sections are offered in one closing line, not built.
- **The appendix follows the same rules**: one topic per appendix, ordered, no notes on notes.
- **When working under another agent**, send the question to that agent to pass to the author; do not answer it yourself.

## Common mistakes

- A "balanced" output that serves two readers because the purpose was never asked.
- Moving every note to the appendix unsorted. A note with no substance is cut; one with substance is appended, or the author is asked whether it may go.
- A caveat that changes the reader's action left in the appendix; it is body.
- Shortening by compressing words (dropped articles, symbols, private labels); the reader then has to decode. Cut content, not grammar.
- Decorating: a colour, an icon or bold that carries no meaning.

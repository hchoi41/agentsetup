---
name: drawio-drill-updown
description: Use when asked for a draw.io (.drawio) diagram of how something works, at one or more abstraction levels, or to add a more abstract ("drill up", "too detailed", "one box", "overview") or more detailed ("drill down", "break this down", "show the steps") sheet to an existing .drawio file. Covers systems, procedures, plugins, pipelines, sets and subsets of data, and documents that describe them. Always structures: parts are drawn inside their whole, and the text inside every box is a headline with bullets and sub-bullets. Themes: minimal (default; flowchart shapes, at most 4 colours), bw (black and white), colorful (roles as colours).
---

# drawio-drill-updown

## Overview

A diagram of a system is a stack of sheets, one per abstraction level, in one `.drawio` file,
most abstract first. Each sheet is written as a JSON spec and compiled by
`python scripts/drawio_spec.py`; the script owns XML, ids, shapes, colours and fit warnings so
every sheet of a file reads the same way. The sheets are related by containment: every box on level k
is the name of a group of boxes on level k+1.

One principle governs every choice below: make the structure visible wherever it is possible.
Structure the page (what contains what, what comes first, what sits under what) and structure the
text inside every box (headline, bullets, sub-bullets). A sentence inside a diagram is the
exception and has to earn its place. See "Structure first".

## Inputs

- `source`: what to draw. A document, a folder of reports, a running system, or an existing `.drawio`.
- `levels` (default 1): how many sheets to produce, counted downward from the top level. A request
  that names no level numbers ("two levels", "an overview and the detail") means L1 to L(N).
- `top` (default L1): the top level. L0 only when the user asks for "max abstraction", "one box",
  "a single box" or "the highest level only".
- `direction` (only for an existing `.drawio`): `up` (add a more abstract sheet) or `down`.
- `theme` (default `minimal`; set the spec's `theme` or pass `--theme`; when the user names none,
  use `minimal`):
  - `minimal`: roles as flowchart shapes, lanes as draw.io swimlanes; black, white, the grey tint,
    and `accent_color` on cells and edges that set `accent`.
  - `bw`: black and white only; shapes and lanes as in `minimal`, the accent a black line of width 4.
  - `colorful`: the look from before themes (roles as colours, lanes as tinted rectangles).
  - `max_colours` (`minimal` only): 3 draws the accent in black, as `bw` does; 4 is the default.
    `max_colors`, `--max-colours` and `--max-colors` also work.
  - `accent_color` (or `accent_colour`): a colour written `#RRGGBB` or `#RGB`, not black, white or
    the grey tint; where it is drawn (`minimal` with 4 colours, `colorful`) it needs a contrast of at
    least 3:1 against white and against the grey tint (a pale accent cannot be seen); any other value
    stops the run. `bw` and `minimal` with 3 colours draw no accent colour and warn that it is ignored.
  - Two authoring rules keep a spec the same in every theme (bulleted boxes, column lanes); they are
    in Procedure step 4.
  - `minimal` and `bw` ignore, with a warning, a `fill`, `stroke` or edge `color` outside the
    palette, and the accent colour on a cell or edge without `accent`; a source with its own colour
    code uses `colorful`. In every theme a key the spec format does not know is ignored with a
    warning that names the nearest valid key.
- A spec written before themes existed now builds in `minimal`; add `"theme": "colorful"` to keep
  its look.

A new multi-sheet file is one spec with several entries in `sheets`, built once. `add` is only for
an existing file.

## The fidelity ladder

| level | sheet name prefix | what the sheet shows | vertex budget | the test a reader can pass afterwards |
|---|---|---|---|---|
| L0 | `L0 One box` | the task as one box: its purpose in one sentence; at most an input and an output arrow | 1 to 4 | can say what it is for |
| L1 | `L1 Overview` | how the task works conceptually: the actors, 3 to 6 components, the main flows between them, one footnote carrying the fact that justifies the design; when two or more actors each own more than one box, one lane per owner, budget permitting | 8 to 25 | can say in one sentence how it works and why it is shaped that way |
| L2 | `L2 Stages and layers` | 3 to 6 stages as columns, 3 to 6 responsibility lanes as rows; a column read top to bottom tells the story of one stage; no gate codes, file names or IDs | 25 to 50 | can say what each party does at each stage |
| L3 | `L3 Detail` | every phase, gate, script, artifact and external system by its real name or ID; cross-lane edges only for the few flows that carry the argument | 60 to 130 | can find any named element of the source |
| L4 | `L4 Implementation` | files, functions, parameters, commands; split across sheets when needed | no cap | can open the code from the box |

Budgets count vertices (boxes, lanes, text), not edges, and not the cells of a generated legend;
`build`, `list` and `validate` print the two counts separately. `validate --ladder` enforces them from the
sheet name prefix. A sheet that misses its budget is at the wrong level: merge or split boxes
rather than renaming the sheet.

Worked example of the three default levels built from one source:
`references/example_plugin_3_levels_minimal.json` (L1, L2 and L3 of one plugin in the default theme:
bulleted text; on L1 three column lanes, one per owner: Max, the plugin (holding its own parts) and
what lies outside it; on L2 and L3 row lanes named after Max, the plugin's parts and the outside
lane, and stage columns; cross-lane edges routed down the channels between lanes or columns;
`database` and `documents` shapes where the source has them; L1 boxes and lanes that link to the L2
sheet, and L2 lanes and stage headers that link to the L3 sheet; a one-sentence subtitle and a
"How to read" note; a generated `legend` on every sheet; builds with no warnings and passes
`validate --ladder`). Copy it. `references/example_L1_minimal.json` is its L1 sheet alone
(without the links, which need the L2 sheet), for a one-sheet request. `references/example_plugin_3_levels.json` and `references/example_L1_overview.json`
are pre-theme `colorful` fixtures kept for regression: their text is run-on, their legends are
hand-written, and the L3 legend has one known fit warning. Do not copy them.

## Structure first (always on)

This is not an option to switch on. Every sheet at every level is structured, whether or not the
request mentions structure.

### On the page

| what the source says | how it is drawn | never |
|---|---|---|
| a whole and its parts (a set and its subsets) | a frame with square corners, title at the top left, the parts as boxes inside it | a box whose sentence lists the parts |
| a total and its components | the total on the headline of the set (its frame, or its one box when the budget forbids its frame or grid; a grid keeps it in its bottom-right cell); each component in its own box or on its own bullet, with its own number. When the headline names a step or a check about the set, the total goes on a bullet and its components under it (see "Common mistakes") | `A 10 \| B 20 \| C 30` on one line, or numbers inside a parenthesis |
| a parent and its children across sheets | each child inside its parent: inside the parent's frame on the same sheet, or in the lane or column named after the parent on the sheet below; the same order on every sheet | children placed where there was room |
| several actors or layers, each owning more than one box | one lane per owner, named after the owner, its boxes inside it (a draw.io swimlane in `minimal` and `bw`) | owners told apart only by position |
| an owner that is also the whole of its boxes (the plugin and its parts) | a lane, like every other owner: an actor or layer that owns boxes is a lane even when it is also their whole. A frame is for a set or a whole that no actor or layer owns (data sets, subsets, a component's internals) | the owner drawn as a frame between lanes |
| a flow from one set to another | the target placed below (or beside) its source, so the arrow is straight | arrows that cross because the boxes are in list order |
| two ways of splitting the same set | a grid: one split on rows, the other on columns, one number per cell | two separate lists the reader has to reconcile |

Two limits apply to the table:

- **Containment comes first.** A box never leaves the lane of its owner or the column of its stage to
  straighten an arrow. Order the boxes inside a lane or column so the flows run straight, and route a
  cross-lane edge with `points` down the channel between columns.
- **The ladder budget wins.** Frames, grid cells and grid headers are vertices and count toward the
  budget. When a frame or a grid would push a sheet over its budget, draw the whole as one box with one
  bullet per part (each with its count). The frame or the grid goes on the sheet below when the request
  includes that level; otherwise the bulleted box is the answer on this sheet.

### Inside a box

1. Line 1 is the headline: the name of the thing, and its count when it has one. No bullet.
2. Every further fact is one bullet. One fact per bullet.
3. The parts, qualifiers or open items of a bullet go one level down as sub-bullets. Two levels are
   the limit; a third level means the box should be split into boxes. When the budget forbids the
   split, keep two levels and move the third to the sheet below; with no sheet below, leave it out.
   Sub-bullets that are the parts of a count add up to it; a count that is not a part (a sample, or
   overlapping counts such as found by A, found by B, found by both) says so in words ("200 of them
   sampled", "1 of them found by both").
4. Legends come from the `legend` cell; its fixed layout (title, then one sample per row with its
   meaning beside it) is exempt from the bullet rule. Notes, footnotes and "how to read" boxes follow
   the rule: their headline says what the box is ("How to read"); a footnote's headline names the
   claim it supports. Draw them as a `box` with role `note`, not as a `text` cell: a `text` cell
   centres its lines vertically and takes no `valign`.
5. Write bullets with the characters given in `references/spec_format.md` ("Structured text").
   Ordinary leading spaces are dropped by draw.io, so the indentation needs no-break spaces.

Example, as the reader sees it (the set as one box, as when the budget forbids its frame; with room,
the set is a frame and its subsets are boxes inside it, and "Origin known 7,486", which has parts of
its own, is a frame in turn around "classified 6,200" and "unclassified 1,286"):

```
Rows 9,041
• Origin known 7,486
     ◦ classified 6,200
     ◦ unclassified 1,286
• Origin unknown 1,555
     ◦ names to check
     ◦ sample check open
```

Where a sentence is still right (keep this list short):

- the sheet's subtitle: the one sentence the reader should be able to say afterwards;
- the L0 box: the task's purpose in one sentence, as the ladder's L0 row says;
- a label of a few words: a sheet or lane title, a column or row header, a grid cell, an edge label;
- a single line of argument that has no parts, such as the L1 footnote when its one fact has no parts.

A box that holds a sentence for any other reason is restructured.

**The structure test** (run it on the rendered PNG): cover the text (for an agent: ignore every
label) and look only at the shapes; the grouping, the order and the parent of every box should
still be readable. Then read only the first line of every box; the headlines alone should make sense.

## Procedure

1. **Fix the levels before drawing.** From the request, set `top` and `levels`; `levels=N` means
   sheets `top, top+1, ..., top+N-1`. For an existing file, run `list`, read the sheet counts
   against the ladder to find the current level, and set the new level from `direction`.
2. **Read the source for the top level first**, and write the one sentence the top sheet must let a
   reader say. That sentence is the sheet's subtitle. Write it before any box.
3. **Pick the roles and the owners.** Decide which role each actor or component gets (`human`, `propose`, `decide`,
   `guard`, `outside`, `record`). Roles are the fixed vocabulary: the same role means the same thing
   on every sheet. The theme draws them: by shape in `minimal` and `bw`, by colour in `colorful`.
   Then the owners: on any sheet, L1 included, when two or more actors or layers each own more than
   one box, give each owner a lane named after it with its boxes inside, budget permitting (a lane
   is one vertex in `minimal` and `bw`).
   When a thing has a conventional symbol (a database, a person, a document, a yes/no check), give
   its box the catalogue `shape` (`database`, `actor`, `document`, `decision`; see "Shapes" in
   `references/spec_format.md`). In `minimal` and `bw` an explicit shape replaces the role's shape,
   so each shape carries only the roles it cannot hide: `actor` on `human`; `decision` on `human` or
   `decide`; `manual_operation` (work a person does) on `human`, `plain`, `outside` or `record`;
   every other shape on `plain`, `outside` or `record`. There the legend reads
   "yes/no check: code decides"; on any other role `build` warns that the shape hides it. A role's
   own shape stated explicitly is the same as no shape. Put a
   `legend` cell on every sheet from L1 down (optional on L0; in `minimal` and `bw`, `build` warns
   when one is missing, so `--strict` fails); it lists the roles and shapes the sheet uses, so do not write a
   legend box by hand. Mark the one path or box the sheet is about with `accent`, if any, and name
   it in the legend's `labels` (`"accent": "whole A: the independent comparison"`).
4. **Write the spec, top sheet first**, per `references/spec_format.md`. For each lower sheet, every
   box must belong to exactly one box of the sheet above. A parent box becomes, on the sheet below,
   one of four things: a lane (an actor or layer), a column (a stage), a single box inside the lane
   of the actor that owns it (an artifact, a question, a verdict), or, for a set, a frame or a grid
   in its owner's lane with the parent's name as its headline. Place every box on two axes:
   - **Ownership** (lanes and frames): every box sits in the lane or frame of its owner, and every
     lane is named after exactly one box or lane of the sheet above, its parent, so the containment
     is visible without a key. A box may have a role different from its lane's (a record in the Max
     lane) and still belongs to that lane's owner.
   - **Stage** (columns): a `stage` header locates boxes along the workflow. It is a header above
     its grid column, never a lane. Stages may first appear on the sheet where the workflow is first
     shown and need no parent above. Lanes run one way per sheet: rows when the stages are columns,
     columns when the stages are rows (then give every stage header `"style": "direction=south;"`,
     so its chevron points down).

   Give each parent box or lane a `"link"` to the sheet below (and an `offpage` box a link up), so a
   click in draw.io drills down or up. Links follow the workflow: one multi-sheet `build` can link
   in both directions; an `add` can link from the new sheet to sheets already in the file; a link
   from an existing parent sheet down to the new one needs that sheet rebuilt (or the link set in
   draw.io), because `add` never rewrites an existing sheet. Write the text of every box as a
   headline plus bullets ("Structure first"), except where that section allows a sentence. On every
   bulleted box set `"align": "left"` and `"valign": "top"`, so its bullets sit at the top left in
   every theme; on every column lane set `"orientation": "column"`, so its title sits across the
   top in every theme (see "Lanes" in `references/spec_format.md`). Settle the nesting before the
   coordinates: bulleted text needs taller boxes (`"h": "auto"` computes the height and `build`
   prints it), and a set drawn as a frame needs room for its title above the boxes inside it.
5. **Compile and validate.**
   ```
   python scripts/drawio_spec.py build SPEC.json -o OUT.drawio --strict --force
   python scripts/drawio_spec.py validate OUT.drawio --ladder --strict
   ```
   Rebuilding a file you built from this spec uses `--force` (without it `build` refuses to
   overwrite); an existing file you did not build takes new sheets through `add` instead.
   `build` and `add` end with `warnings: N`; `--strict` exits 1 when N is above 0 (`build` still
   writes the file; `add` leaves it unchanged), and `validate --strict` exits 1 on any `ERR` or
   `warn` line. A non-zero exit, or any stop, means: fix the spec (step 6) and run again until both
   exit 0.
   For an existing file:
   ```
   python scripts/drawio_spec.py add SPEC.json --into FILE.drawio --position last --strict
   python scripts/drawio_spec.py validate FILE.drawio --ladder --strict
   ```
   `--position first` for a drill-up, `last` for a drill-down; always pass it (without it the sheet
   goes first). `add` never rewrites an existing sheet and never mixes themes or accents: it takes
   the file's theme, accent and `max_colours`, so leave them out of the spec. How the file keeps its
   theme, and what `add` and `validate` check, is in `references/spec_format.md` ("The file keeps its
   theme" and "Colour check").
6. **Fix every warning**: text that clips is shortened or its box enlarged (font never below 8);
   two boxes that partly overlap, or a legend over any box, are moved apart; a box inside a frame
   that starts above the frame's headline or sits within 10 px of its other sides is moved; a box
   that reaches into a lane's title strip is moved right of it (or below it, in a column lane), and
   one within 10 px of a lane's other sides is moved in or the lane enlarged; an `"h": "auto"` box
   that grows past the bottom of its frame or lane means that frame or lane is enlarged (and what
   lies below it moved down); a lane title with no room gets a wider `gutter`; an `actor` whose
   caption leaves the page, its lane or frame, or covers a neighbour is moved or given room; a cell
   outside the page (a rotated one is measured as drawn, turned) is moved or the page enlarged; two
   boxes of different roles drawn alike get a different outline (`strokeWidth=2`) or their roles'
   own shapes; in `bw`, a dashed box that is not a frame loses its dash; an edge that cannot be seen
   on a black frame loses its own colour (or the frame its fill); a sheet without a legend gets one; a
   budget miss means the sheet is at the wrong level.
7. **Render and look.** `python scripts/drawio_spec.py export OUT.drawio --out DIR` writes one PNG
   per sheet (it finds draw.io on PATH, in Program Files, or the Microsoft Store build; set
   `DRAWIO_EXE` for any other install). Open each PNG with the image reader and check, in this
   order: text cut off or run together, or touching a shape's slant, bars or wave; an edge crossing
   a box, or ending short of a shape's outline; two labels on top of each other;
   an arrow entering the wrong side; a box whose text is a run-on line where bullets were possible;
   arrows that cross because of the order of the boxes; a title sitting on a rounded corner (give
   the frame square corners), or a headline sitting on one (see "Structured text" in
   `references/spec_format.md`). Then run the structure test ("Structure first").
   Fix the spec, rebuild, re-export; two rounds are normal.
   Only when no renderer exists does the script say so, and then the report says the sheets were
   validated but not seen.
8. **File placement**: in a hub project, the file goes in `20.PROCESS/` with the project naming
   rule; a drill of an existing file stays in that file.

## Quick reference

| want | do |
|---|---|
| new diagram, one sheet | `levels=1`, top L1; one JSON spec; `build` |
| "max abstraction only" | top L0, `levels=1`: one box, one sentence |
| "N levels" | sheets L1..L(N); L0 only when asked |
| existing file is too detailed | `list` to find its level, `add` an L(k-1) sheet at `--position first` |
| existing file needs the steps | `add` an L(k+1) sheet at `--position last`; always pass `--position` (without it the sheet goes first) |
| rebuild a file you built | `build ... --force` |
| cell outside page, dangling edge, duplicate id | `validate` reports it; fix the spec, rebuild |
| no renderer | `export` says so; describe the sheets in words, do not claim to have seen them |
| a box holds more than one fact | headline, one bullet per fact, sub-bullets for its parts (`T()` helper in `references/spec_format.md`) |
| a set and its subsets | a square-cornered frame with the subsets inside; counts on the headlines |
| the same set split two ways | a grid of cells: rows = one split, columns = the other, one number per cell |
| look of the file | `theme`: `minimal` (default; roles as shapes), `colorful` (roles as colours), `bw` (black and white) |
| three colours even with an accent | `"max_colours": 3` (or `--max-colours 3`) with `minimal`: the accent is drawn as a black line of width 4 (`minimal` without any accent is already three colours) |
| a legend | a `legend` cell; `labels` renames a role's meaning |
| a database, a person, a document, a yes/no check | the catalogue `shape`: `database`, `actor` (role `human`), `document`, `decision` (role `human` or `decide`) |
| point at the parent box on the sheet above | the catalogue `shape` `offpage`, labelled with the parent's name, with `"link"` to that sheet |
| click from a box or lane to the sheet that details it | `"link": "<sheet name>"` on the box or lane |
| a machine check that a build is clean | `--strict` on `build` or `add`: exit 1 on any warning; then `validate FILE --ladder --strict`, which exits 1 on any `ERR` or `warn` line (the check after `add`) |
| a box height for bulleted text | `"h": "auto"` |
| the one path that matters | `"accent": true` on its boxes and edges |
| a `warn: ... outside the palette ...; ignored` line from `build` | the `fill`, `stroke` or edge `color` override was dropped and the file was written; drop the override, or use `colorful` for the source's own colour code |
| a stop with `style key ... is outside the ... palette` | nothing was written; remove that key from the cell's `style` and rebuild |
| several owners, each with more than one box | one lane per owner, its boxes inside it |
| "not allowed in theme" from `build` | the `style` string uses a key outside the allow-list (spec_format, "Themes"); drop it |

## Common mistakes

- **Structure hidden in a sentence.** A box that says "9,041 rows: 6,912 matched, 574 partial,
  1,555 unknown; classified, names and sample check remain". The reader has to parse it. Put the
  total on a bullet, the three parts under it, the state on a second bullet and what remains under
  that. The same goes for legends and notes.
  ```
  Origin check
  • Rows 9,041
       ◦ matched 6,912
       ◦ partial 574
       ◦ unknown 1,555
  • State: classified
       ◦ names remain
       ◦ sample check remains
  ```
- **Detail leaking upward.** Gate codes, file names and script names on an L1 or L2 sheet. The
  budget catches the count; the rule catches the kind: above L3, nothing carries an ID.
- **Levels that do not nest.** A box on L2 that has no parent on L1, or an L1 component that
  vanishes on L2. Name each L2 lane after an L1 box or lane; stage columns may first appear on L2.
- **A new colour or shape per sheet.** Roles are the vocabulary, not decoration: one theme per file
  (`add` keeps the file's theme and accent), and a role keeps its shape (`minimal`, `bw`) or colour
  (`colorful`) on every sheet. If the source has its own colour code, use `colorful`, say so in the
  legend's `labels`, and keep it on every sheet.
- **Rewriting the existing sheet** to make room. `add` refuses name collisions for this reason.
- **Claiming a rendered check that did not happen.** Without a draw.io CLI the validation is XML,
  ids, bounds, fit and budgets, and the report says exactly that.

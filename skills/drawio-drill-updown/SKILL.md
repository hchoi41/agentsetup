---
name: drawio-drill-updown
description: Use when asked for a draw.io (.drawio) diagram of how something works, at one or more abstraction levels, or to add a more abstract ("drill up", "too detailed", "one box", "overview") or more detailed ("drill down", "break this down", "show the steps") sheet to an existing .drawio file. Covers systems, procedures, plugins, pipelines, sets and subsets of data, and documents that describe them. Always structures: parts are drawn inside their whole, and the text inside every box is a headline with bullets and sub-bullets.
---

# drawio-drill-updown

## Overview

A diagram of a system is a stack of sheets, one per abstraction level, in one `.drawio` file,
most abstract first. Each sheet is written as a JSON spec and compiled by
`python scripts/drawio_spec.py`; the script owns XML, ids, colours and fit warnings so every
sheet of a file reads the same way. The sheets are related by containment: every box on level k
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

A new multi-sheet file is one spec with several entries in `sheets`, built once. `add` is only for
an existing file.

## The fidelity ladder

| level | sheet name prefix | what the sheet shows | vertex budget | the test a reader can pass afterwards |
|---|---|---|---|---|
| L0 | `L0 One box` | the task as one box: its purpose in one sentence; at most an input and an output arrow | 1 to 4 | can say what it is for |
| L1 | `L1 Overview` | how the task works conceptually: the actors, 3 to 6 components, the main flows between them, one footnote carrying the fact that justifies the design | 8 to 25 | can say in one sentence how it works and why it is shaped that way |
| L2 | `L2 Stages and layers` | 3 to 6 stages as columns, 3 to 6 responsibility lanes as rows; a column read top to bottom tells the story of one stage; no gate codes, file names or IDs | 25 to 50 | can say what each party does at each stage |
| L3 | `L3 Detail` | every phase, gate, script, artifact and external system by its real name or ID; cross-lane edges only for the few flows that carry the argument | 60 to 130 | can find any named element of the source |
| L4 | `L4 Implementation` | files, functions, parameters, commands; split across sheets when needed | no cap | can open the code from the box |

Budgets count vertices (boxes, lanes, text), not edges. `validate --ladder` enforces them from the
sheet name prefix. A sheet that misses its budget is at the wrong level: merge or split boxes
rather than renaming the sheet.

Worked example of the three default levels built from one source: `references/example_plugin_3_levels.json`
(L1, L2 and L3 of one plugin; builds and validates as is). `references/example_L1_overview.json`
is its L1 sheet alone, for a one-sheet request. The worked examples predate "Structure first": copy
their levels and geometry, not their run-on box text. Once their text becomes bullets, the height rule
in `references/spec_format.md` ("Structured text") sets each box's height; move what sits below it
and enlarge the page as needed.

## Structure first (always on)

This is not an option to switch on. Every sheet at every level is structured, whether or not the
request mentions structure.

### On the page

| what the source says | how it is drawn | never |
|---|---|---|
| a whole and its parts (a set and its subsets) | a frame with square corners, title at the top left, the parts as boxes inside it | a box whose sentence lists the parts |
| a total and its components | the total on the headline of the set (its frame, or its one box when the budget forbids its frame or grid; a grid keeps it in its bottom-right cell); each component in its own box or on its own bullet, with its own number. When the headline names a step or a check about the set, the total goes on a bullet and its components under it (see "Common mistakes") | `A 10 \| B 20 \| C 30` on one line, or numbers inside a parenthesis |
| a parent and its children across sheets | each child inside its parent: inside the parent's frame on the same sheet, or in the lane or column named after the parent on the sheet below; the same order on every sheet | children placed where there was room |
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
4. Legends, notes, footnotes and "how to read" boxes follow the same rule. Their headline says what
   the box is ("Legend", "How to read"); a footnote's headline names the claim it supports. Draw them
   as a `box` with role `note`, not as a `text` cell: a `text` cell centres its lines vertically and
   takes no `valign`.
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
3. **Pick the roles.** Decide which role each actor or component gets (`human`, `propose`, `decide`,
   `guard`, `outside`, `record`). Roles are the only colours; the same role means the same thing on
   every sheet. Put a legend box on every sheet.
4. **Write the spec, top sheet first**, per `references/spec_format.md`. For each lower sheet, every
   box must belong to exactly one box of the sheet above. A parent box becomes, on the sheet below,
   one of four things: a lane (an actor or layer), a column (a stage), a single box inside the lane
   of the actor that owns it (an artifact, a question, a verdict), or, for a set, a frame or a grid
   in its owner's lane with the parent's name as its headline. Name lanes and columns
   after the parent boxes so the containment is visible without a key. Write the text of every box
   as a headline plus bullets ("Structure first"), except where that section allows a sentence, and
   settle the nesting before the coordinates:
   bulleted text needs taller boxes, and a set drawn as a frame needs room for its title above the
   boxes inside it.
5. **Compile and validate.**
   ```
   python scripts/drawio_spec.py build SPEC.json -o OUT.drawio
   python scripts/drawio_spec.py validate OUT.drawio --ladder
   ```
   For an existing file use `add --into FILE.drawio --position first` for a drill-up and
   `--position last` for a drill-down. Existing sheets are never rewritten.
6. **Fix every warning**: text over capacity is shortened or its box enlarged (font never below 8);
   a budget miss means the sheet is at the wrong level.
7. **Render and look.** `python scripts/drawio_spec.py export OUT.drawio --out DIR` writes one PNG
   per sheet (it finds draw.io on PATH, in Program Files, or the Microsoft Store build; set
   `DRAWIO_EXE` for any other install). Open each PNG with the image reader and check, in this
   order: text cut off or run together; an edge crossing a box; two labels on top of each other;
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
| existing file needs the steps | `add` an L(k+1) sheet at `--position last` |
| cell outside page, dangling edge, duplicate id | `validate` reports it; fix the spec, rebuild |
| no renderer | `export` says so; describe the sheets in words, do not claim to have seen them |
| a box holds more than one fact | headline, one bullet per fact, sub-bullets for its parts (`T()` helper in `references/spec_format.md`) |
| a set and its subsets | a square-cornered frame with the subsets inside; counts on the headlines |
| the same set split two ways | a grid of cells: rows = one split, columns = the other, one number per cell |

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
  vanishes on L2. Name L2 lanes and columns after L1 boxes.
- **A new colour per sheet.** Colours are roles, not decoration. If the source has its own colour
  code, say so in the legend and keep it on every sheet.
- **Rewriting the existing sheet** to make room. `add` refuses name collisions for this reason.
- **Claiming a rendered check that did not happen.** Without a draw.io CLI the validation is XML,
  ids, bounds, fit and budgets, and the report says exactly that.

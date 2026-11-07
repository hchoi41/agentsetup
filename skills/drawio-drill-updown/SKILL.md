---
name: drawio-drill-updown
description: Use when asked for a draw.io (.drawio) diagram of how something works, at one or more abstraction levels, or to add a more abstract ("drill up", "too detailed", "one box", "overview") or more detailed ("drill down", "break this down", "show the steps") sheet to an existing .drawio file. Covers systems, procedures, plugins, pipelines, and documents that describe them.
---

# drawio-drill-updown

## Overview

A diagram of a system is a stack of sheets, one per abstraction level, in one `.drawio` file,
most abstract first. Each sheet is written as a JSON spec and compiled by
`python scripts/drawio_spec.py`; the script owns XML, ids, colours and fit warnings so every
sheet of a file reads the same way. The sheets are related by containment: every box on level k
is the name of a group of boxes on level k+1.

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
is its L1 sheet alone, for a one-sheet request.

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
   one of three things: a lane (an actor or layer), a column (a stage), or a single box inside the
   lane of the actor that owns it (an artifact, a question, a verdict). Name lanes and columns
   after the parent boxes so the containment is visible without a key.
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
   an arrow entering the wrong side. Fix the spec, rebuild, re-export; two rounds are normal.
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

## Common mistakes

- **Detail leaking upward.** Gate codes, file names and script names on an L1 or L2 sheet. The
  budget catches the count; the rule catches the kind: above L3, nothing carries an ID.
- **Levels that do not nest.** A box on L2 that has no parent on L1, or an L1 component that
  vanishes on L2. Name L2 lanes and columns after L1 boxes.
- **A new colour per sheet.** Colours are roles, not decoration. If the source has its own colour
  code, say so in the legend and keep it on every sheet.
- **Rewriting the existing sheet** to make room. `add` refuses name collisions for this reason.
- **Claiming a rendered check that did not happen.** Without a draw.io CLI the validation is XML,
  ids, bounds, fit and budgets, and the report says exactly that.

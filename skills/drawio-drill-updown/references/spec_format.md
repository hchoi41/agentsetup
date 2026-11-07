# Spec format for `scripts/drawio_spec.py`

One JSON file = one or more sheets of a draw.io file. The script does the XML, ids, colours and
the fit warnings; you supply boxes, lanes and edges with coordinates.

```json
{
  "file": "optional/default/output.drawio",
  "sheets": [
    {
      "id": "l1",                      // short, unique inside the file; used as id prefix
      "name": "L1 Overview",           // MUST start with the level prefix L0..L4 (ladder check)
      "width": 1000, "height": 620,    // page size; validate warns about cells outside it
      "grid": {"x0": 180, "pitch": 320, "col_width": 300},   // optional column grid
      "cells": [ ... ]
    }
  ]
}
```

## Cells

| type | required | optional |
|---|---|---|
| `box` (default) | `text`, `y`, `h`, and `x` or `col` | `id`, `w` or `span`, `role`, `font` (default 9), `bold`, `align`, `valign`, `dashed`, `fill`, `stroke`, `square`, `style` |
| `lane` | `x`, `y`, `w`, `h` | `text` (lane title, drawn in a left gutter), `role` (lane tint), `gutter` (default 150), `fill` |
| `text` | `text`, `x`, `y`, `w`, `h` | `id`, `font`, `bold` (default true), `align` |
| `edge` | `from`, `to` (cell ids) | `id`, `label`, `role` or `color`, `dashed`, `width`, `exit` [x,y], `entry` [x,y], `points` [[x,y],...], `label_at` (-1 source .. 0 middle .. 1 target), `label_offset` [dx,dy], `font`, `style` |

A newline in any `text` or `label` becomes a line break in draw.io (the compiler writes the
entity-encoded `<br>` that draw.io expects); never put raw HTML in a value.

`points` are absolute waypoints the orthogonal router passes through: use them to send a
cross-lane edge down the channel between two columns instead of through a box. `label_at` and
`label_offset` move the label along the edge and then by pixels, so it lands in empty space
(a strip between lanes) rather than on a neighbouring box.

`col` and `span` need `grid`: x = x0 + pitch * col, w = pitch * (span - 1) + col_width.
`exit` / `entry` are fractions of the box (0,0 top-left; 1,1 bottom-right); set them whenever an
edge crosses a lane, or draw.io will pick a side you did not mean.

Geometry rules that keep edges routable:
- `grid.x0` ≥ lane `x` + `gutter` (default 150), so columns start right of the lane titles.
- `pitch − col_width` ≥ 20 px, so vertical edges have a channel between columns.
- Lanes are tinted by their `role`; a box inside a lane carries its own `role`, which may differ
  (a yellow question box inside a blue skills lane is normal). Lane tint never changes edge colours.
- Vertices are counted for the ladder budgets: boxes, lanes, lane titles and text cells all count.

## Roles (fixed palette, same on every sheet)

| role | colour | meaning |
|---|---|---|
| `human` | yellow | a person decides: gate, question, verdict |
| `propose` | blue | an LLM or skill inside the system proposes, drafts, explains; never passes or fails |
| `decide` | green | deterministic code decides; exit code is the verdict |
| `guard` | orange | policy layer: hooks, caps, allow-lists |
| `outside` | purple | a system the diagram uses but does not contain, including an LLM run by another tool or vendor |
| `record` | white, dashed green | an artifact or audit record |
| `note` | white, grey | legend, caption, footnote |
| `stage` | blue, bold | column header |
| `plain` | grey | neutral component when none of the above applies |

Use `fill` / `stroke` only when a source has its own established colour code, and then say so in
the legend box.

## Commands

```
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio          # new file (refuses to overwrite)
python scripts/drawio_spec.py add SPEC.json --into FILE.drawio --position first|last|before:NAME|after:NAME
python scripts/drawio_spec.py list FILE.drawio
python scripts/drawio_spec.py validate FILE.drawio --ladder           # ids, dangling edges, page bounds, level budgets
python scripts/drawio_spec.py export FILE.drawio --format png         # only if a draw.io CLI is installed
```

`add` never rewrites an existing sheet: a name or id collision stops the run. It writes `FILE.drawio.bak`
before inserting. Compressed sheets (draw.io's default save format) are read for `list` and `validate`
and left untouched by `add`.

## Fit rule of thumb

Capacity in characters ≈ (w / (0.5 × font)) × (h / (1.35 × font)). `build` prints a warning for
each box over capacity; shorten the text or enlarge the box, do not drop the font below 8.

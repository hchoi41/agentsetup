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

## Structured text (headline, bullets, sub-bullets)

Every box with more than one fact is written as a headline plus bullets (SKILL.md, "Structure first").

- Bullet: `• ` (U+2022 and a space) at the start of the line.
- Sub-bullet: five no-break spaces (U+00A0), then `◦ ` (U+25E6 and a space). Ordinary leading
  spaces are dropped by draw.io's HTML labels; no-break spaces survive.
- Write each no-break space as the escape `\u00a0`, in a JSON string and in Python alike, never as the
  character itself: a literal no-break space looks like a space, and a copied or retyped line turns
  it into one without warning.
- When a script writes the spec, keep `json.dump`'s default `ensure_ascii=True`: it writes every
  non-ASCII character as an escape, the no-break space included; `ensure_ascii=False` writes the
  bare character.
- Headline: line 1, no bullet. Lines are separated by `\n` in the JSON string.
- Set `"align": "left", "valign": "top"` on bulleted boxes; centred bullets do not line up. A box
  with its text at the top left whose shorter side is over about 120 px also takes
  `"style": "absoluteArcSize=1;arcSize=12;"`: draw.io rounds a corner by 15% of the box's shorter
  side (26 px at 176 px), which cuts into the headline. The style fixes the radius at 6 px at any
  size, so the box stays rounded and does not read as a frame (square corners mark a frame).

```json
{"id": "rows", "text": "Rows 9,041\n• Origin known 7,486\n\u00a0\u00a0\u00a0\u00a0\u00a0◦ classified 6,200\n\u00a0\u00a0\u00a0\u00a0\u00a0◦ unclassified 1,286\n• Origin unknown 1,555\n\u00a0\u00a0\u00a0\u00a0\u00a0◦ names to check\n\u00a0\u00a0\u00a0\u00a0\u00a0◦ sample check open",
 "x": 40, "y": 80, "w": 200, "h": 100, "role": "record", "align": "left", "valign": "top"}
```

Helper for a script-built spec, so every box is written the same way:

```python
NBSP = "\u00a0"

def T(headline, *items):
    """Box text: a string is a bullet; a list right after it holds that bullet's sub-bullets."""
    lines = [headline]
    for it in items:
        if isinstance(it, (list, tuple)):
            lines += [NBSP * 5 + "◦ " + s for s in it]
        else:
            lines.append("• " + it)
    return "\n".join(lines)

# the JSON example above, built with T():
box_text = T("Rows 9,041",
             "Origin known 7,486", ["classified 6,200", "unclassified 1,286"],
             "Origin unknown 1,555", ["names to check", "sample check open"])
```

Geometry:

- **Height of a bulleted box**: h ≥ lines × 1.35 × font + 12 (font 9: about 12 px per line plus
  12 for padding). Count wrapped lines (a line holds about w / (0.5 × font) characters, as in the
  fit rule below: 44 at w 200 and font 9), not only `\n`; the fit warning counts characters, so a box
  can pass it and still clip a long list. Check the PNG. Avoid wraps in bullets: a wrapped line
  restarts at the box's left edge, so a bullet's second line loses its indentation. Shorten the
  bullet or widen the box; a wrap is acceptable only in the headline.
- **A set frame**: a `box` with `"square": true`, `"align": "left"`, `"valign": "top"`, and the set's
  headline (name and count) as its text. Its role is `plain`, or the role of the whole when it has
  one (a set of records or artifacts is `record`); never `note`, which means a legend or footnote.
  A frame is a box like any other: a fact about the whole that is not a part goes on a bullet under
  its headline. Leave a gap above the first box inside of at least lines × 1.35 × font + 12 px,
  counting every rendered line of the frame's own text, wraps included (one line at font 9: 24 px),
  and ≥ 10 px padding on the other sides. List the frame before the boxes inside it, so it is drawn
  underneath.
- **Two splits of one set (grid)**: a header row of `stage` boxes for the column split; a header
  column of `plain` boxes with `"bold": true` for the row split; one `plain` box per cell holding a
  single number. Place the cells with explicit `x` and `y` (or with `col` when the sheet's `grid` is
  not already used for stage columns). Number cells share one `w` and `h`; the cells of one column
  share `x` and `w`, the cells of one row share `y` and `h`, and the row-header column may be wider
  than the number columns. Row and column totals go in a last column and a last row, set `bold`.
  The top-left corner cell holds the set's name, as a bold `plain` box; the bottom-right cell holds
  the grand total. A grid needs no frame around it.

## Fit rule of thumb

Capacity in characters ≈ (w / (0.5 × font)) × (h / (1.35 × font)). `build` prints a warning for
each box over capacity; shorten the text or enlarge the box, do not drop the font below 8.

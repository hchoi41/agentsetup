# Spec format for `scripts/drawio_spec.py`

One JSON file = one or more sheets of a draw.io file. The script does the XML, ids, shapes, colours
and the fit warnings; you supply boxes, lanes and edges with coordinates.

```json
{
  "file": "optional/default/output.drawio",
  "theme": "minimal",                  // optional: minimal (default) | colorful | bw
  "accent_color": "#1F5FBF",           // optional: the one accent colour (default shown); or accent_colour
  "max_colours": 4,                    // optional, minimal only: 3 or 4 (default 4); or max_colors
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
| `box` (default) | `text`, `y`, `h` (a number, or `"auto"`), and `x` or `col` | `id`, `w` or `span`, `role`, `shape`, `accent`, `font` (default 9), `bold`, `headline` (default true), `align`, `valign`, `dashed`, `fill`, `stroke`, `square`, `style` |
| `lane` | `x`, `y`, `w`, `h` | `text` (lane title, drawn in a left gutter), `role` (lane tint in `colorful`), `gutter` (default 150), `fill` |
| `text` | `text`, `x`, `y`, `w`, `h` | `id`, `font`, `bold` (default true), `align` |
| `edge` | `from`, `to` (cell ids) | `id`, `label`, `role` or `color`, `accent`, `dashed`, `width`, `exit` [x,y], `entry` [x,y], `points` [[x,y],...], `label_at` (-1 source .. 0 middle .. 1 target), `label_offset` [dx,dy], `font`, `style` |
| `legend` | `x`, `y` | `id`, `w` (default 220), `labels` {role or shape name or `accent`: meaning}, `title` (default "Legend"), `font` |

A key not listed for the top level, a sheet (`id`, `name`, `width`, `height`, `grid`, `cells`) or a
cell's type is ignored, and `build` and `add` warn, naming the sheet, the cell and the nearest valid key.

A newline in any `text` or `label` becomes a line break in draw.io (the compiler writes the
entity-encoded `<br>` that draw.io expects). In `minimal` and `bw` text is literal: markup such as
`<b>` and entities such as `&copy;` show as typed; the compiler adds only its own line breaks and
bold headlines. In `colorful`, box, text, lane-title and edge labels are encoded as they were before
themes existed, so markup and entities in them stay active HTML; legend text is literal.

`points` are absolute waypoints the orthogonal router passes through: use them to send a
cross-lane edge down the channel between two columns instead of through a box. `label_at` and
`label_offset` move the label along the edge and then by pixels, so it lands in empty space
(a strip between lanes) rather than on a neighbouring box.

`col` and `span` need `grid`: x = x0 + pitch * col, w = pitch * (span - 1) + col_width. Without a
grid whose `pitch` and `col_width` are above 0, `build` stops and names the sheet and cell;
`validate` reports any cell of width or height 0 or less as an error.
`exit` / `entry` are fractions of the box (0,0 top-left; 1,1 bottom-right); set them whenever an
edge crosses a lane, or draw.io will pick a side you did not mean.

Geometry rules that keep edges routable:
- `grid.x0` ≥ lane `x` + `gutter` (default 150), so columns start right of the lane titles.
- `pitch − col_width` ≥ 20 px, so vertical edges have a channel between columns.
- A box inside a lane carries its own `role`, which may differ from the lane's (a question box
  owned by Max inside the skills lane is normal). In `colorful` lanes are tinted by their `role`;
  in `minimal` every lane is grey, in `bw` white. Lane tint never changes edge colours.
- Vertices are counted for the ladder budgets: boxes, lanes, lane titles and text cells all count.
  Legend cells do not; `build`, `list` and `validate` print them as `legend=` beside `vertices=`.
- `build` warns for every two vertices that overlap by 5 px or more in both directions without
  one containing the other, and for a legend that covers any vertex, wholly or in part (a lane or
  frame may hold a legend). Move one of them.
- In `minimal` and `bw`, frames and lanes are draw.io containers: each box, frame or text cell that
  lies wholly inside one is saved as its child (moving the frame in draw.io moves its contents).
  Coordinates in the spec stay absolute; the script converts them. The generated legend is a
  container too, holding its rows. `colorful` keeps every cell on the page layer.

## Themes

| theme | roles told apart by | colours in the file |
|---|---|---|
| `minimal` (default) | shape | black `#000000` (strokes, text, edges), white `#FFFFFF` (box fill), grey `#F2F2F2` (frames, lanes, `stage`, `note`), and `accent_color` when a cell or edge sets `accent`; nothing else. Four at most, three with `max_colours` 3 |
| `colorful` | colour (the original look) | the role palette below; no limit |
| `bw` | shape and line style: frames dashed, accent a black stroke of width 4 | `#000000` and `#FFFFFF` only |

`--theme` on `build` overrides the spec's `theme`; with neither, the theme is `minimal`. An unknown
theme stops the run. A spec written before themes existed now builds in `minimal`; add
`"theme": "colorful"` to keep its look. In `minimal` and `bw`, `build` warns when a box reads as a
hand-written colour key (a colour word followed by `box`, `boxes`, `=`, `:` or a dash, such as
"yellow = Max decides") and the sheet has no `legend` cell: the key no longer matches the drawing.
Grey and gray do not count in `minimal`, whose tint is grey.

- **Accent** (`minimal`, `colorful`): `"accent": true` on a box or an edge draws it in
  `accent_color` with stroke width 2. Use it for the one path or box the sheet is about. In `bw`,
  and in `minimal` with `max_colours` 3, the accent is a black stroke of width 4, and `build` warns
  when a box or edge without accent is wider than 2, since it could be read as the accent. The
  accent is applied after every override and `style` string, so it is drawn as the legend shows it.
  An `accent_color` equal to black, white or the grey tint stops the run.
- **`max_colours`** (`minimal` only; spec key `max_colours` or `max_colors`, CLI `--max-colours` or
  `--max-colors`): 3 or 4. 4 (default) adds `accent_color` to black, white and grey; 3 keeps black,
  white and grey and draws the accent as in `bw`. Any other value, or a value that is not a whole
  number, stops the run with a message (black and white only is `--theme bw`). Two spellings with
  different values stop it too.
- **Colour check.** `build`, `add` and `validate` read every cell's `style` attribute from the
  parsed XML (compressed sheets decoded) and count every declaration of every `*Color` key
  (`black`, `white` and `#fff` are the same colours as `#000000` and `#FFFFFF`). They also count each
  sheet's page background and the colours in a label's active HTML (a tag's `color` attribute, and
  `color` or `background` in its `style`); markup the compiler escaped is text and does not count.
  A colour outside
  the theme's palette, or five colours in any theme but `colorful`, stops `build` and `add` before
  anything is written and is an `ERR` in `validate`, which uses the theme, accent and
  `max_colours` the file records. `build` prints the theme and the colours it emitted.
- **Overrides.** In `minimal` and `bw`, a `fill`, `stroke` or edge `color` inside the palette is
  used; any other is ignored with a warning. The accent colour counts as inside the palette only on
  a cell or edge that sets `accent` (a lane never does), so the legend's accent row explains every
  use of it; in a `style` string on any other cell it stops the run. A source with its own colour
  code uses `colorful`, and says so in the legend's `labels`.
- **`style` strings** in `minimal` and `bw` pass an allow-list: geometry and edge routing (`size`,
  `fixedSize`, `flipH`, `flipV`, `direction`, `exitX`, `entryX`, `endArrow`, ...), spacing,
  typography (`fontSize`, `fontStyle`, `fontFamily`, `align`, ...), perimeter, arc (`rounded`,
  `arcSize`), `dashed`, `strokeWidth`, `rotation`, `shape=` with a draw.io name behind a catalogue
  shape (the legend then shows that shape), and the colour keys `fillColor`, `strokeColor`,
  `fontColor`, `labelBackgroundColor`, `labelBorderColor` with a value in the palette. Anything
  else stops the run and names the key: `image`, `glass`, `sketch`, stencils outside the catalogue (`shape=mxgraph...`),
  bare named styles (`blue;`), gradients, `shadow`, opacity. Tokens are trimmed, and the trimmed
  string is what the file holds, so the check reads exactly what draw.io reads.
- **Edges** in `minimal` and `bw` are black; an edge `role` no longer colours it. `dashed`, `width`
  and `accent` still apply.
- **Frames** (`"square": true`) are plain square-cornered rectangles in every theme, whatever their
  role: grey in `minimal`, white with a dashed outline in `bw`, the role's colours in `colorful`. A
  `shape` on a frame is dropped. In `minimal` and `bw`, after nesting, `build` warns when a child
  starts above the frame's headline (naming the minimum y) or sits closer than 10 px to its left,
  right or bottom side, and when an `"h": "auto"` box grows past its frame's bottom. A `rounded` key
  in a frame's `style` is dropped with a warning that names it.
- **Headlines.** In `minimal` and `bw` the first line of a box with more than one line, and of
  every frame, is bold, so the headline stands apart without colour. `"headline": false` turns it off for one box;
  `"bold": true` still makes the whole box bold. `colorful` is unchanged.
- **Alignment.** In `minimal` and `bw` a frame, and a box whose text has more than one line, default
  to `"align": "left"`, `"valign": "top"`; an explicit value wins. `decision`, `cloud` and
  `terminator` always centre: an `align` or `valign` on the cell, or an `align` or `verticalAlign` in
  its `style`, is dropped with a warning. One-line boxes and `colorful` default to centred.
- **The file keeps its theme.** `build` records the theme, accent and `max_colours` as attributes
  on `<mxfile>` (`dsTheme`, `dsAccent`, `dsMaxColours`; none for `colorful` with the default
  accent). In `minimal` and `bw` it also writes them as style keys on the first vertex of each sheet
  (`dsTheme=minimal;dsAccent=#1F5FBF;dsMaxColours=4;`), which survive a save in draw.io. `add`,
  `list` and `validate` read the `<mxfile>` attributes first, then those style keys; a file with
  neither is `colorful`. A record that names an unknown theme, or holds a bad accent or
  `max_colours`, stops the run, as the same value on the command line or in a spec would. `add` uses
  the record as its defaults. A
  `--theme` or spec `theme` that differs from the file's stops the run and leaves the file
  untouched; so does an `accent_color` that differs from the file's, and new sheets are compiled
  with the file's accent. The colour check counts the existing sheets plus the new ones. `list` and
  `validate` print the theme and the file's colours.

## Roles (fixed vocabulary, same on every sheet)

| role | shape (`minimal`, `bw`) | colour (`colorful`) | meaning |
|---|---|---|---|
| `human` | manual input (slanted top) | yellow | a person decides: gate, question, verdict |
| `propose` | rounded rectangle (alternate process) | blue | an LLM or skill inside the system proposes, drafts, explains; never passes or fails |
| `decide` | predefined process (side bars) | green | deterministic code decides; exit code is the verdict |
| `guard` | hexagon (preparation) | orange | policy layer: hooks, caps, allow-lists |
| `outside` | double-bordered rectangle | purple | a system the diagram uses but does not contain, including an LLM run by another tool or vendor |
| `record` | document (wavy bottom) | white, dashed green | an artifact or audit record |
| `note` | note (folded corner), grey in `minimal` | white, grey | legend, caption, footnote |
| `stage` | step (chevron), grey in `minimal` | blue, as `propose`; bold only when the box sets `"bold": true` | column header |
| `plain` | rectangle (process) | grey | neutral component when none of the above applies |

## Shapes (catalogue, all themes)

`"shape": "<name>"` on a box draws that draw.io built-in instead of the role's shape. Pick one when
the thing has a conventional symbol: a database, a person, a document, a yes/no check. In
`colorful` the box keeps its role's colours; in `minimal` and `bw` the shape replaces the role's
shape. Each shape carries the roles it does not hide: `actor` on `human`; `decision` on `human` or
`decide`; every other shape on `plain`, `outside` or `record`. On those roles the legend reads
`<shape meaning>: <role meaning>` ("yes/no check: code decides"); on any other role `build` warns
that the shape hides the role, and the legend gives the shape's meaning alone. A role's own shape
(the "Roles" table, such as `predefined` on `decide`) stated explicitly is the same as no shape: no
warning, and the legend gives the role's meaning. An unknown name stops the run and lists the valid
names.

| name | draws | text width | top/bottom allowance |
|---|---|---|---|
| `process` | rectangle | full | 0 |
| `rounded` | rounded rectangle, 6 px corners | full | 0 |
| `decision` | diamond | half: one short line, centred (forced) | half the height |
| `terminator` | stadium (start or end) | 0.9, less 16 px: one short line, centred (forced) | 0 |
| `data` | parallelogram (data in or out) | full, less 24 px | 0 |
| `document` | wavy bottom | full | 12% of the height |
| `documents` | several documents (a set of files) | 0.9 | 25% of the height |
| `predefined` | side bars | full, less 36 px (the bars and their spacing) | 0 |
| `manual_input` | slanted top | full | 10 px |
| `manual_operation` | trapezoid, narrow at the bottom (work a person does) | full, less 24 px | 0 |
| `preparation` | hexagon | full, less 20 px | 0 |
| `database` | cylinder | full | 18 px |
| `stored_data` | curved sides (stored data) | full, less 24 px | 0 |
| `delay` | round right side (wait or delay) | full, less half the height | 0 |
| `offpage` | pentagon pointing down (continues on another sheet) | full | 25% of the height |
| `actor` | stick figure; the text sits below it | n/a: give it about 40 × 70 and connect it from a side, since its label is below | n/a |
| `cloud` | cloud | 0.6: one short line, centred (forced) | 40% of the height |
| `note` | folded corner | full, less 12 px | 0 |
| `step` | chevron | full, less 24 px | 0 |
| `double` | double-bordered rectangle | full, less 6 px | 3 px |

Each shape carries its own spacing so top-left text clears slants, bars and points; edges attach to
the drawn outline (draw.io's outline perimeter for the slanted, curved and wavy shapes, the rounded
perimeter for `terminator`). One exception: the concave right side of `stored_data` does not attach
on the outline (an edge stops at the curve's tips, short of the drawn curve), so connect it from
another side. `decision`, `cloud` and `terminator` always centre their text; `build` warns when
such a box asks for left or top alignment or holds more than one line. `offpage` is the way to point
at a parent box on the sheet above: label it with the parent's name and connect it to the boxes
that belong to that parent.

## Legend

`{"type": "legend", "x": 700, "y": 585, "w": 330, "labels": {"human": "Max decides"}}` draws a frame
headed "Legend" with one row per look used by the boxes on that sheet: roles first, then catalogue
`shape`s, then the accent if any box or edge sets it (frames are listed only in `colorful`). Each
row is one cell: a sample drawn with the shape and paint the boxes finally have (overrides and a
`shape=` in `style` included; colours compared after normalising), with its meaning beside it. The
accent sample is a box outline, or a line when only edges carry the accent. Two meanings that share one look become one row showing
both ("audit evidence; customer input"); boxes of one role painted two ways give two rows and a
warning. Default meanings: `human` a person decides, `propose` an LLM or skill proposes, `decide`
code decides, `guard` policy layer, `outside` outside system, `record` record or artifact, `note`
note, `stage` stage, `plain` component, `accent` highlighted; `labels` overrides any of them (keys are
role names, shape names or `accent`). Name the highlighted path in `labels.accent`.

The title and row heights follow `font` and the wrapped length of each meaning, so the height is
known only after `build`, which prints the legend's box (`legend: sheet ... at x= y= w= h=`); about
26 px per row at font 9. A `w` that leaves under 40 px for the meanings stops the run. Legend cells
carry the style key `dsLegend=1` and are left out of the ladder count by `build`, `list` and
`validate`, which print them separately. In `minimal` and `bw` the legend frame is a container and
its rows are its children, so it moves as one unit in draw.io. Every sheet from L1 down gets a
legend; on L0 it is optional. In a `minimal` or `bw` file, `validate --ladder` warns on an L1 to L4
sheet without one.

## Commands

```
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio          # new file (refuses to overwrite; --force)
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio --theme bw   # --theme minimal|colorful|bw overrides the spec
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio --max-colours 3   # minimal: 3 or 4 colours (or --max-colors)
python scripts/drawio_spec.py add SPEC.json --into FILE.drawio --position first|last|before:NAME|after:NAME   # keeps the file's theme and accent
python scripts/drawio_spec.py list FILE.drawio                       # theme, colours, then vertices / legend / edges per sheet
python scripts/drawio_spec.py validate FILE.drawio --ladder           # recorded palette, ids, dangling edges, sizes, page bounds, level budgets, legends
python scripts/drawio_spec.py export FILE.drawio --format png         # only if a draw.io CLI is installed
```

`add` never rewrites an existing sheet: a name or id collision stops the run. Two sheets with one
name in one spec stop `build` and `add` alike, and `validate` reports an existing file that has
them, since `before:NAME` and `after:NAME` could not tell them apart. `add` writes `FILE.drawio.bak`
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
- Bulleted boxes sit at the top left: `minimal` and `bw` do this for any box of more than one line;
  in `colorful` set `"align": "left", "valign": "top"`, since centred bullets do not line up. In
  `colorful`, a box with its text at the top left whose shorter side is over about 120 px also takes
  `"style": "absoluteArcSize=1;arcSize=12;"`: draw.io rounds a corner by 15% of the box's shorter
  side (26 px at 176 px), which cuts into the headline. The style fixes the radius at 6 px at any
  size, so the box stays rounded and does not read as a frame. How a frame is marked depends on the
  theme: square corners in `colorful`, the grey fill in `minimal`, a dashed outline in `bw`. In
  `minimal` and `bw` the `rounded` shape already has 6 px corners and the other shapes carry their
  own spacing.

```json
{"id": "rows", "text": "Rows 9,041\n• Origin known 7,486\n\u00a0\u00a0\u00a0\u00a0\u00a0◦ classified 6,200\n\u00a0\u00a0\u00a0\u00a0\u00a0◦ unclassified 1,286\n• Origin unknown 1,555\n\u00a0\u00a0\u00a0\u00a0\u00a0◦ names to check\n\u00a0\u00a0\u00a0\u00a0\u00a0◦ sample check open",
 "x": 40, "y": 80, "w": 200, "h": 100, "role": "record"}
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

- **Height of a bulleted box**: h ≥ lines × 1.35 × font + 12 + the shape's allowance (the
  "Shapes" table; a share of the height divides instead: h ≥ (lines × 1.35 × font + 12) / (1 − share)).
  Count wrapped lines (a line holds about w / (0.55 × font) characters: about 40 at w 200 and font 9),
  not only `\n`. `"h": "auto"` does this count for you and `build` prints each computed height; place
  what sits below the box from the printed number. Avoid wraps in bullets: a wrapped line
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
- **Two splits of one set (grid)**: a header row of `plain` boxes with `"bold": true` for the
  column split, and a header column of the same for the row split; one `plain` box per cell holding
  a single number. `stage` is for columns that are stages: in `minimal` and `bw` it is a chevron,
  which reads as a sequence that the columns of a grid do not have. Place the cells with explicit `x` and `y` (or with `col` when the sheet's `grid` is
  not already used for stage columns). Number cells share one `w` and `h`; the cells of one column
  share `x` and `w`, the cells of one row share `y` and `h`, and the row-header column may be wider
  than the number columns. Row and column totals go in a last column and a last row, set `bold`.
  The top-left corner cell holds the set's name, as a bold `plain` box; the bottom-right cell holds
  the grand total. A grid needs no frame around it.

## Fit check

`build` wraps each box's text at the shape's text width (the "Shapes" table) and warns when the
wrapped lines, plus the shape's allowance, no longer fit the box's height: the text would clip.
Wide and full-width characters (Hangul, CJK) count 1 em, combining marks none, in boxes and legend
rows alike. A `delay`'s right inset (half its height less 6 px) and its height are computed
together, so `"h": "auto"` and the same height given as a number draw and check the same. The
warning names the height the box needs. Shorten the text or enlarge the box (or use `"h": "auto"`);
do not drop the font below 8. The width estimate is approximate: check the PNG.

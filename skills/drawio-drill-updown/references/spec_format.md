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
| `box` (default) | `text`, `y`, and `x` or `col` | `h` (a number, or `"auto"`; not `"auto"` on a frame (`square`) or an `actor`; default 60), `id`, `w` (default: the grid's `col_width`, else 120) or `span`, `role`, `shape`, `accent`, `font` (default 9), `bold`, `headline` (default true), `align`, `valign`, `dashed`, `fill`, `stroke`, `square`, `style`, `link` (a sheet name of this file; see "Shapes") |
| `lane` | `x`, `y`, `w`, `h` | `id`, `text` (lane title, in the title strip; a lane without `text` has no strip; see "Lanes"), `orientation` (`"row"` or `"column"`; default: a lane taller than wide is a column), `font` (title size, at least 10), `role` (lane tint in `colorful`), `gutter` (size of the title strip: its width on a row lane, default 150; its height on a column lane, default the title's height), `fill` (`colorful` only: the whole lane; in `minimal` and `bw` the body is always white and a `fill` is ignored with a warning), `link` (as on a box: a click on the lane, or on its title in `colorful`, opens that sheet) |
| `text` | `text`, `y`, `h`; `x` or `col`; `w` or `span` | `id`, `font`, `bold` (default true), `align` |
| `edge` | `from`, `to` (cell ids) | `id`, `label`, `role` or `color`, `accent`, `dashed`, `width`, `exit` [x,y], `entry` [x,y], `points` [[x,y],...], `label_at` (-1 source .. 0 middle .. 1 target), `label_offset` [dx,dy], `font`, `style` |
| `legend` | `y`, and `x` or `col` | `id`, `w` (default 220, which needs nothing from the grid: a legend placed by `col` needs only `pitch`) or `span`, `labels` {role or shape name, `frame` or `accent`: meaning}, `title` (default "Legend"), `font` |

A key not listed for the top level, a sheet (`id`, `name`, `width`, `height`, `grid`, `cells`), a
sheet's `grid` (`x0`, `pitch`, `col_width`) or a cell's type is ignored, and `build` and `add` warn,
naming the sheet, the cell and the nearest valid key (a misspelt `grid` key would otherwise move
every column placed by `col`).

A newline in any `text` or `label` becomes a line break in draw.io (the compiler writes the
entity-encoded `<br>` that draw.io expects). In `minimal` and `bw` text is literal: markup such as
`<b>` and entities such as `&copy;` show as typed; the compiler adds only its own line breaks and
bold headlines. In `colorful`, box, text, lane-title and edge labels are encoded as they were before
themes existed, so markup and entities in them stay active HTML; legend text is literal.

`points` are absolute waypoints (page coordinates) the orthogonal router passes through: use them
to send a cross-lane edge down the channel between two columns instead of through a box. `label_at` and
`label_offset` move the label along the edge and then by pixels, so it lands in empty space
(a strip between lanes) rather than on a neighbouring box.

`col` and `span` need `grid`: x = x0 + pitch * col, w = pitch * (span - 1) + col_width. A cell needs
only the grid values of the formula it uses: x from `col` needs `pitch` above 0 (`x0` defaults to 0);
a cell placed by `col` or `span` that gives no `w` needs `col_width` above 0, and `pitch` too when
`span` is over 1. A cell that gives its own `x` or `w` needs nothing from the grid for it. When a
needed value is missing, `build` stops in `minimal` and `bw` and names the sheet and cell; `validate`
reports any cell of width or height 0 or less as an error. `colorful` builds a spec exactly as the
script did before themes existed: a missing grid value counts as 0 and a cell without `w` takes the
grid's `col_width`, else 120; a cell `type` it does not know is drawn as a box (with a warning), and
two sheets may share a name when no cell has a `link`.

Ids, references and `style` strings may hold any character (`R&D`, `fontFamily="Times New Roman"`);
the script escapes them for the XML. An id is a literal string: `A&B` and `A&amp;B` are two ids, and
a `link` opens the sheet whose id is exactly the one given. In `colorful` an id that is already valid
XML is written as typed, as before themes; when two ids would then read back as one (`A&B` and
`A&amp;B`), `build` stops and names them.
`exit` / `entry` are fractions of the box (0,0 top-left; 1,1 bottom-right); set them whenever an
edge crosses a lane, or draw.io will pick a side you did not mean.

Geometry rules that keep edges routable:
- `grid.x0` ≥ lane `x` + `gutter` (default 150 on a titled row lane; an untitled lane has no
  strip), so columns start right of the lane titles.
- `pitch − col_width` ≥ 20 px, so vertical edges have a channel between columns.
- A box sits in the lane of its owner. It carries its own `role`, which may differ from the lane's
  (a record in the Max lane) and still belongs to that lane's owner; the stage it belongs to is
  shown by its column, not by its lane. In `colorful` lanes are tinted by their `role`;
  in `minimal` and `bw` every lane is white with a black outline, and in `minimal` its title strip
  is grey, so a grey frame or note inside a lane stands out. Lane tint never changes edge colours.
- A lane's contents start where its title strip ends: on a row lane at x ≥ lane `x` + `gutter`, on
  a column lane at y ≥ lane `y` + its strip's height. In `minimal` and `bw`, `build` warns for each
  box, frame, text cell or legend inside a lane that reaches into the strip, and names the first
  free x or y; `validate` warns the same for any draw.io swimlane in an existing file. On the other
  sides, as in a frame, `build` warns when a child sits closer than 10 px to the lane's edge, and
  when an `"h": "auto"` box grows past the lane's bottom (it names the lane: enlarge it).
- Vertices are counted for the ladder budgets: boxes, lanes, lane titles and text cells all count.
  In `minimal` and `bw` a lane and its title are one cell, so a titled lane counts once; in
  `colorful` its title is a cell of its own and counts too. Legend cells do not count; `build`,
  `list` and `validate` print them as `legend=` beside `vertices=`.
- `build` warns for every two vertices that overlap by 5 px or more in both directions without
  one containing the other, and for a legend that covers any vertex, wholly or in part (a lane or
  frame may hold a legend). Move one of them.
- `build` and `validate` warn for a box, frame, text cell, lane or legend that reaches outside the
  page. A cell whose `style` sets a `rotation` is measured as draw.io draws it: by the bounding
  rectangle of its turned shape (a 180 × 40 box turned 90° covers 40 × 180 about the same centre).
  `build` uses the same turned rectangle for whether a lane or frame holds the cell, for the
  title-strip and padding checks and for the overlap check (`validate` for its title-strip check),
  so a turned box that leaves its lane or covers a neighbour warns; the geometry written to the
  file is the one the spec gives.
- In `minimal` and `bw`, frames and lanes are draw.io containers: each box, frame, text cell or
  legend that lies wholly inside one is saved as its child (moving the frame in draw.io moves its contents).
  An edge is saved in the container draw.io itself would give it: the smallest lane or frame that
  holds both its ends (a container at one end holds itself; an edge from a cell to itself takes
  that cell's container); an edge whose ends share only the page stays on the page. Its `points` are
  converted to that container's coordinates, so moving a lane in draw.io moves the bends of the
  edges inside it too; a `label_offset` is a displacement from the label's place and stays as given.
  The drawing is the same either way. Coordinates in the spec stay absolute; the script converts
  them. The generated legend is a container too, holding its rows. `colorful` keeps every cell and
  edge on the page layer. `validate`, `list` and `add` read edges wherever they are saved, files
  built with every edge on the page layer included.

### Lanes

A lane is an owner's region: an actor or a layer, with the boxes it owns inside it. In `minimal`
and `bw` a lane is one draw.io swimlane: a container whose title strip carries the lane title as
the cell's own label (bold, centred in the strip, wrapped at its width), so title and lane move and
resize together in draw.io. The strip is grey (`#F2F2F2`) in `minimal` and white in `bw`; the body
is always white, so the edges and outlines (black) and the frames and notes (grey) inside it stay
visible; a lane's `fill` is ignored there with a warning. The outline and the title are black.

- **Row or column.** `"orientation": "row"` gives a row lane: its strip is on the left, `gutter` wide
  (default 150), and the title is written across it as in any box (draw.io's `direction=north`
  turns the strip to the left side and keeps the text horizontal). `"orientation": "column"` gives a
  column lane: its strip is on top, as tall as the title's wrapped lines need (lines × 1.35 × font +
  12; one line at font 10: 26 px), or `gutter` px when given. Without `orientation`, a lane taller
  than it is wide is a column lane, any other a row lane. Drawing, the fit check and the strip check
  all use the same answer.
- **Stages are not lanes.** A stage is a `stage` header above its grid column, never a lane. Lanes
  run one way per sheet: rows when the stages are columns, columns when the stages are rows. When
  the stages are rows, give every stage header of the sheet `"style": "direction=south;"`, so its
  chevron points down; the legend then shows one `stage` row, drawn pointing down.
- **Untitled.** A lane without `text` has no title strip, whatever its `gutter` says; its contents
  keep 10 px from every side.
- **Order.** List each lane before the boxes inside it: `colorful` draws cells in list order, so a
  lane listed later covers them.
- **Fit.** A row lane's title wraps at the strip's width and must fit the lane's height; a column
  lane's title wraps at the lane's width and must fit the strip. A title that does not fit warns
  (`lane title ... clips`); shorten it, widen `gutter`, or make the lane taller. A strip too small
  for even one line (a row strip narrower than one character, a column strip lower than one line)
  warns that the title has no room; widen `gutter`.
- **Contents** start where the strip ends and keep 10 px from the other sides (see "Geometry
  rules"); a child that reaches into the strip, or comes closer to another side, warns.
- **In an existing file** `validate`, `list` and `add` read a draw.io swimlane in either spelling
  draw.io writes, `swimlane;` or `shape=swimlane;`, the same way: strip, children and colours (a
  spec still writes a lane as a `lane` cell; `shape=swimlane` in a cell's `style` stops `build`).
- On a titled lane, a `gutter` that is not a number, below 0, or as large as the lane's width (row)
  or height (column) stops the run; so does an `orientation` other than `"row"` or `"column"`. A
  `colorful` row lane is the exception for a number out of range: it is drawn as before themes,
  with a warning.

In `colorful` a lane is a tinted rectangle with a separate title text cell, not a swimlane. A lane
that sets `"orientation": "column"` has its title across the top, in a strip as high as its
swimlane strip in `minimal` and `bw` (or `gutter` px), so the same spec places its boxes below the
title in every theme. Any other lane is drawn as before themes existed: its title in a gutter on
the left, whatever its shape. A lane that is a column only by its shape (taller than wide, no
`orientation`) therefore gets a left gutter over the boxes placed below a top strip, and `build`
warns; set `"orientation": "column"` on every column lane.

## Themes

| theme | roles told apart by | colours in the file |
|---|---|---|
| `minimal` (default) | shape | black `#000000` (strokes, text, edges), white `#FFFFFF` (box fill, lane body), grey `#F2F2F2` (frames, lane title strips, `stage`, `note`, the legend frame), and `accent_color` when a cell or edge sets `accent`; nothing else. Four at most, three with `max_colours` 3 |
| `colorful` | colour (the original look) | the role palette below; no limit |
| `bw` | shape and line style: frames dashed, accent a black stroke of width 4 | `#000000` and `#FFFFFF` only |

`--theme` on `build` overrides the spec's `theme`; with neither, the theme is `minimal`. An unknown
theme stops the run. A spec written before themes existed now builds in `minimal`; add
`"theme": "colorful"` to keep its look. In `minimal` and `bw`, `build` warns when a box reads as a
hand-written colour key (a colour word followed by `box`, `boxes`, `=`, `:` or a dash, such as
"yellow = Max decides") and the sheet has no `legend` cell: the key no longer matches the drawing.
Grey and gray do not count in `minimal`, whose tint is grey. In `minimal` and `bw`, `build` (and
`add`) also warns for every sheet whose name starts with `L1` to `L4` and has no `legend` cell, since
roles are told apart by shape only; `--strict` then fails.

- **Accent** (`minimal`, `colorful`): `"accent": true` on a box or an edge draws it in
  `accent_color` with stroke width 2. `accent` is a JSON boolean, `true` or `false`; any other value
  (the string `"false"`, `1`, `null`) stops the run and names the cell or edge. Use it for the one path or box the sheet is about. In `bw`,
  and in `minimal` with `max_colours` 3, the accent is a black stroke of width 4, and `build` warns
  when a box or edge without accent is wider than 2, since it could be read as the accent. In every
  theme the accent is applied after every override and `style` string (the cell's `stroke`, an
  edge's own `color` and `width`, a raw `strokeColor` or `strokeWidth`, a key written after a space
  included), so it is drawn exactly as the legend's accent sample shows it. Only a box or an edge can set `accent`. For the same reason
  the legend groups an accented box without the stroke colour and width the accent replaced, so it
  shares its role's row and adds no warning.
  `accent_color` is read like every other colour: `#RRGGBB` or `#RGB` in any case (`#00f` is
  `#0000FF`). One equal to black, white or the grey tint stops the run. Where the accent colour is
  drawn (`colorful`, and `minimal` with `max_colours` 4), so does one whose contrast against white or
  against the grey tint is below 3:1 (WCAG), since a pale accent cannot be seen; the message names
  the contrast. In `bw` and in `minimal` with `max_colours` 3 the accent is a black line, so the
  colour is checked as a colour only and `build` warns that it is ignored, as `add` does in `bw`. A
  colour name other than black and white is not read.
- **`max_colours`** (`minimal` only; spec key `max_colours` or `max_colors`, CLI `--max-colours` or
  `--max-colors`): 3 or 4. 4 (default) adds `accent_color` to black, white and grey; 3 keeps black,
  white and grey and draws the accent as in `bw`. Any other value, or a value that is not a whole
  number, stops the run with a message (black and white only is `--theme bw`). Two spellings with
  different values stop it too.
- **Colour check.** `build`, `add` and `validate` read every cell's `style` attribute from the
  parsed XML (compressed sheets decoded) and count every declaration of every `*Color` key
  (a swimlane's `swimlaneFillColor` included; `black`, `white` and `#fff` are the same colours as `#000000` and `#FFFFFF`). They also count each
  sheet's page background and the colours in a label's HTML when its cell has `html=1` (a tag's
  `color` attribute, and `color`, `background` or `background-color` in its `style`, quoted or
  not, as hex, `rgb()` or a CSS colour name); with `html=0`, and for markup the compiler escaped, the
  label is text and does not count. In `minimal` and `bw` an existing file is also checked against
  an allow-list, since a hand edit in draw.io can add paint that a colour count cannot see. A page
  (`<mxGraphModel>`) may hold only the attributes the compiler writes (`dx`, `dy`, `grid`,
  `gridSize`, `guides`, `tooltips`, `connect`, `arrows`, `fold`, `page`, `pageScale`, `pageWidth`,
  `pageHeight`, `math`, `shadow`) and `background`, with `shadow` only 0 and `background` absent,
  `none` or a palette colour; any other attribute, such as a `backgroundImage`, is a breach named in
  the message. A cell's style may hold only the keys the compiler writes and the keys the `style`
  allow-list below permits, under the same value rules (a colour in the palette, `gradientColor`
  only `none`, opacity keys only 100, `shape=` only a catalogue shape, or `swimlane`, draw.io's other
  spelling of a lane), and only the bare names the compiler writes (`text`,
  `swimlane`, the names behind the catalogue shapes) or draw.io's paint-free names (`edgeLabel`,
  `group`, `ellipse`, `rhombus`, `triangle`, `line`). Anything else is a breach named by its key:
  `glass`, `sketch`, `image`, `shadow`, a gradient, partial opacity, a named style such as `blue;`.
  An HTML label may hold only paint-free markup, as the compiler and draw.io's label editor write it:
  the tags `b`, `strong`, `i`, `em`, `u`, `br`, `div`, `span`, `p`, `font`, `ul`, `ol`, `li`, `sub`,
  `sup`; the attributes `align`, `face` and `size` (on `font`) and `style`, whose properties must be
  `text-align`, `font-size`, `font-weight`, `font-style`, `font-family`, `line-height`,
  `text-decoration` (without a colour), `white-space`, or a `margin` or `padding` property. Any
  other tag (`img`, `svg`, ...), attribute (`color`, `bgcolor`, ...) or property (`color`,
  `background`, `border`, `text-shadow`, `filter`, `opacity`, ...) is a breach, named in the message.
  The three lists come from one place in the script, so every file the compiler writes passes.
  Compressed and uncompressed sheets are read alike.
  A breach stops `add` before any backup or write, is an `ERR` in `validate`, which uses the theme,
  accent and `max_colours` the file records, and a `warn` line in `list`. `build` prints the theme
  and the colours it emitted.
- **Overrides.** In `minimal` and `bw`, a `fill`, `stroke` or edge `color` inside the palette is
  used; any other is ignored with a warning (a lane's `fill` is always ignored: its body is white).
  The accent colour counts as inside the palette only on
  a cell or edge that sets `accent` (a lane never does), so the legend's accent row explains every
  use of it; in a `style` string on any other cell it stops the run. A source with its own colour
  code uses `colorful`, and says so in the legend's `labels`.
- **`style` strings** in `minimal` and `bw` pass an allow-list: geometry and edge routing (`size`,
  `fixedSize`, `flipH`, `flipV`, `direction`, `exitX`, `entryX`, `endArrow`, ...), spacing,
  typography (`fontSize`, `fontStyle`, `fontFamily`, `align`, ...), perimeter, arc (`rounded`,
  `arcSize`), `dashed`, `strokeWidth`, `rotation`, `resizable` and `points` (which draw.io writes on
  a label typed onto an edge), `shape=` with a draw.io name behind a catalogue
  shape (the legend then shows that shape), and the colour keys `fillColor`, `strokeColor`,
  `fontColor`, `labelBackgroundColor`, `labelBorderColor` with a value in the palette. Anything
  else stops the run and names the key: `image`, `glass`, `sketch`, stencils outside the catalogue (`shape=mxgraph...`),
  bare named styles (`blue;`, `swimlane;`; a lane is the `lane` cell), swimlane keys such as
  `swimlaneFillColor`, `gradientColor` other than `none`, `shadow`, opacity below 100. `html`
  is not on the list: the compiler always writes `html=1`, and a raw `html=0` warns and is dropped.
  In every theme, `colorful` included, the script's own markers `dsTheme`, `dsAccent`,
  `dsMaxColours` and `dsLegend` are reserved: a `style` that holds one (copied from a built file)
  stops `build` and `add`, naming the cell and the key, before anything is written, since `list`,
  `validate` and `add` read the file's theme and its legend from them.
  A raw `rounded` that squares a `rounded` box, or `double` that removes the second border of a
  `double` box, warns, since the box no longer reads as its role's shape; the legend then gives it
  a row of its own (see "Legend"). A raw `fontSize`, `fontStyle`, `spacing...`, `labelWidth`, `size`
  or `fixedSize` key is measured by the fit check (see "Fit check"). Tokens are trimmed, and
  the trimmed string is what the file holds, so the check reads exactly what draw.io reads.
- **Edges** in `minimal` and `bw` are black; an edge `role` no longer colours it. `dashed`, `width`
  and `accent` still apply. An edge saved inside a lane or frame (see "Geometry rules") runs on that
  container's body: on a black body (a frame with `"fill": "black"`) an edge of the default colour is
  drawn white instead; one whose colour is its own (a `color`, a raw `strokeColor`, or the accent,
  which in `bw` is a black line) and has under 3:1 contrast with the body warns and names the repair.
- **Frames** (`"square": true`) are plain square-cornered rectangles in every theme, whatever their
  role: grey in `minimal`, white with a dashed outline in `bw`, the role's colours in `colorful`. A
  `shape` on a frame is dropped. In `minimal` and `bw` the legend gives frames one row, drawn as the
  frame is drawn, meaning "group of parts" (`labels.frame` renames it). In `bw`, where the dash is
  the frame's cue, a box that is not a frame and sets `dashed` (the cell key or `dashed=1` in its
  `style`) warns that it reads as a frame. In `minimal` and `bw`, after nesting, `build` warns when a child
  starts above the frame's headline (naming the minimum y) or sits closer than 10 px to its left,
  right or bottom side, and when an `"h": "auto"` box grows past its frame's bottom. A `rounded` key
  in a frame's `style` is dropped with a warning that names it; in `bw` so is a `dashed` key, and a
  `"dashed": false` on the frame warns and is ignored, since the dashed border is the only frame cue.
- **Text colour.** A box whose `style` sets no `fontColor` gets black or white text, whichever
  contrasts more with what is behind its text (both colours are in every palette; `colorful` boxes
  without a `shape` keep the look from before themes). What is behind the text is its
  `labelBackgroundColor` when the `style` sets one, else the box's own fill, else, for a box with
  `fillColor=none`, the nearest painted surface holding it (found after nesting: a lane's title
  strip or body, a filled frame, a filled box, else the page). A `fontColor` equal to that colour
  warns. In `minimal` and `bw`, text without a fill of its own (a `text` cell, and an `actor`'s
  caption, which sits below the figure) takes the colour that contrasts with the nearest painted
  surface behind it, or with the actor's own `labelBackgroundColor`. A white lane inside a black
  frame keeps its text black. An `actor` whose `style` sets `fontColor` keeps it. An edge label with
  a `labelBackgroundColor` in its `style` gets white text on a dark one, and a `fontColor` equal to
  it warns.
- **Headlines.** In `minimal` and `bw` the first line of a box with more than one line, and of
  every frame, is bold, so the headline stands apart without colour. `"headline": false` turns it off for one box;
  `"bold": true` still makes the whole box bold. `colorful` is unchanged.
- **Alignment.** In `minimal` and `bw` a frame, and a box whose text has more than one line, default
  to `"align": "left"`, `"valign": "top"`; an explicit value wins. `decision`, `cloud` and
  `terminator` always centre: an `align` or `valign` on the cell, or an `align` or `verticalAlign` in
  its `style`, is dropped with a warning. In `colorful` the same holds for a box whose `shape` names
  one of the three: its `style` is kept as written and the centring is written after it, with the
  same warning when they conflict. One-line boxes and `colorful` default to centred, so set
  `"align": "left", "valign": "top"` on every bulleted box: the `minimal` and `bw` output is the
  same, and the spec also builds with top-left bullets in `colorful`. The worked examples do this.
- **The file keeps its theme.** `build` records the theme, accent and `max_colours` as attributes
  on `<mxfile>` (`dsTheme`, `dsAccent`, `dsMaxColours`; none for `colorful` with the default
  accent). In `minimal`, `bw`, and `colorful` with another accent, it also writes them as style keys
  (`dsTheme=minimal;dsAccent=#1F5FBF;dsMaxColours=4;`) on each sheet's layer cell, which no edit of
  the content deletes, and on its first vertex; both survive a save in draw.io. `add`, `list` and
  `validate` resolve each setting on its own: the `<mxfile>` attribute when present, else the value
  the sheets' records hold (a layer's record, then a first-vertex record of files built before the
  layer record), else the default; a file whose theme no record holds is `colorful`. So a file that
  lost only `dsMaxColours` from `<mxfile>` keeps the 3 its sheets record. The records are read by
  parsing the style, so a missing last `;` or a following key makes no difference. `validate`
  compares, setting by setting, every sheet's layer with its first-vertex record, every sheet with
  the `<mxfile>` record and the sheets with each other, and reports each disagreement as an `ERR`
  naming the sheets and the settings (a page imported from another file can bring one); `add` stops
  on it before any backup or write, and checks the assembled file (existing sheets plus the new
  ones) again before it writes. A record that names an unknown theme, or holds a bad accent or
  `max_colours`, stops the run, as the same value on the command line or in a spec would. `add` uses
  the record as its defaults. `--theme` and the spec's `theme` are each checked against the
  file's: either one that differs stops the run and leaves the file untouched. So does an
  `accent_color` that differs from the file's, except in `bw`, which never draws the accent colour
  and only warns; new sheets are compiled with the file's accent. A `max_colours` for a file that
  is not `minimal` warns and is ignored, as in `build`. The colour check counts the existing sheets
  plus the new ones. `list` and `validate` print the theme and the file's colours.

## `colorful` and specs written before themes

`colorful` is the look from before themes, and the test suite checks it byte for byte. It builds
specs generated to use only the keys the script before themes knew (one or two sheets; grids with
any subset of `x0`, `pitch` and `col_width`; boxes, lanes, text cells, cells of unknown types and
edges with every key of that time, odd values included) and the two older examples
(`example_L1_overview.json`, `example_plugin_3_levels.json`), once with that script (kept as
`scripts/baseline_fixture/drawio_spec_baseline.py`) and once with `--theme colorful`, and compares
the bytes of the two files: every spec the old script builds must give the same file. The newer
behaviours act only on keys that did not exist before themes:

- **Accent**: `accent` on a box or an edge, and `accent_color`.
- **Catalogue shapes** and their centring: `shape` on a box. A `shape=` written in a box's own
  `style`, as before themes, keeps the old drawing.
- **Orientation**: `orientation` on a lane; a lane without it keeps the left title gutter.
- **Page links**: `link` on a box or a lane.
- **Generated legend**: a cell of type `legend`. The script before themes drew a cell of any type
  it did not know as a plain box; `legend` is the one such type that now draws something else.
- **Text and height**: `headline`, and `"h": "auto"` (the script before themes stopped on it).
- **New warnings** (unknown keys, page bounds, fit, a gutter out of range, a column lane without
  `orientation`, an unknown type): printed on the console only; the file's bytes do not change.
- **New stops**: a raw `style` holding a reserved marker key (`dsTheme`, `dsAccent`, `dsMaxColours`,
  `dsLegend`, written only by the script), and a spec with no sheets. The comparison's generator
  writes one or two sheets and none of those keys, so neither can occur in it.

## Roles (fixed vocabulary, same on every sheet)

| role | shape (`minimal`, `bw`) | colour (`colorful`) | meaning |
|---|---|---|---|
| `human` | manual input (slanted top) | yellow | a person decides: gate, question, verdict |
| `propose` | rounded rectangle (alternate process) | blue | an LLM or skill inside the system proposes, drafts, explains; never passes or fails |
| `decide` | predefined process (side bars) | green | deterministic code decides; exit code is the verdict |
| `guard` | hexagon (preparation) | orange | policy layer: hooks, caps, allow-lists |
| `outside` | double-bordered rectangle (draw.io built-in, no ANSI symbol) | purple | a system the diagram uses but does not contain, including an LLM run by another tool or vendor |
| `record` | document (wavy bottom) | white, dashed green | an artifact or audit record |
| `note` | note (folded corner; draw.io built-in, no ANSI symbol), grey in `minimal` | white, grey | legend, caption, footnote |
| `stage` | step (chevron; draw.io built-in, no ANSI symbol), grey in `minimal` | blue, as `propose`; bold only when the box sets `"bold": true` | column header |
| `plain` | rectangle (process) | grey | neutral component when none of the above applies |

The other six role shapes are ANSI/ISO 5807 flowchart symbols. `human` is drawn as manual input,
not as manual operation (the symbol for work a person does), because manual input's flat bottom and
full-width body leave room for bulleted text, which the trapezoid, narrow at the bottom, does not;
`"shape": "manual_operation"` on a `human` box is allowed and keeps the role (see "Shapes").
ISO 5807 does have a symbol for an
annotation (an open bracket), but `note` keeps the folded corner: a note holds bulleted text, which
reads better in a closed shape, and an open bracket beside a grey frame is easy to misread.

## Shapes (catalogue, all themes)

Every shape below ships with draw.io, and so does the swimlane that draws a lane in `minimal` and
`bw` (see "Lanes"); no extra library is loaded. `documents` comes from draw.io's bundled flowchart
stencil set (`mxgraph.flowchart.multi-document`), the one stencil the catalogue allows; it renders
in the desktop export like the others.

`"shape": "<name>"` on a box draws that draw.io built-in instead of the role's shape. Pick one when
the thing has a conventional symbol: a database, a person, a document, a yes/no check. In
`colorful` the box keeps its role's colours; in `minimal` and `bw` the shape replaces the role's
shape. Each shape carries the roles it does not hide: `actor` on `human`; `decision` on `human` or
`decide`; `manual_operation` on `human`, `plain`, `outside` or `record`; every other shape on
`plain`, `outside` or `record`. On those roles the legend reads
`<shape meaning>: <role meaning>` ("yes/no check: code decides"); on any other role `build` warns
that the shape hides the role, and the legend gives the shape's meaning alone. A role's own shape
(the "Roles" table, such as `predefined` on `decide`) stated explicitly is the same as no shape: no
warning, and the legend gives the role's meaning. An unknown name stops the run and lists the valid
names. In `minimal` and `bw`, two boxes of different roles that are drawn alike (a `human`
`decision` and a `decide` `decision`, or a `plain` box whose `style` rounds it like `propose`)
cannot be told apart by the reader, and `build` warns with both ids. Two remedies clear the
warning: give one of them a different outline in its `style` (`strokeWidth=2`, or `dashed=1` in
`minimal`), or use the roles' own shapes. Naming who decides in each headline helps the reader, but
the check compares drawings, not text, so it does not clear the warning.

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
| `actor` | stick figure; the text sits below it | n/a: give it about 40 × 70 and connect it at a hand, since its label is below | n/a |
| `cloud` | cloud | 0.6: one short line, centred (forced) | 40% of the height |
| `note` | folded corner | full, less 12 px | 0 |
| `step` | chevron | full, less 24 px | 0 |
| `double` | double-bordered rectangle | full, less 6 px | 3 px |

Each shape carries its own spacing so top-left text clears slants, bars and points; edges attach to
the drawn outline (draw.io's outline perimeter for the slanted, curved and wavy shapes, the rounded
perimeter for `terminator`). Two exceptions, where a side midpoint is not on the drawn outline: the
concave right side of `stored_data` (an edge stops at the curve's tips, short of the drawn curve),
so connect it from another side; and `actor`, whose hands are at about `[1, 0.3333]` and
`[0, 0.3333]`, so connect it there (`"exit": [1, 0.3333]` on an edge leaving it). An `actor`'s
caption is measured (wrapped at the figure's width, wider for a longer word) and `build` warns when
it leaves the page, reaches past the bottom of the lane or frame holding the actor, or overlaps
another cell; `validate` warns when it leaves the page. The figure's own size and connection
points are unchanged. `decision`, `cloud`
and `terminator` always centre their text; `build` warns when such a box asks for left or top
alignment or holds more than one line. `offpage` is the way to point at a parent box on the sheet
above: label it with the parent's name and connect it to the boxes that belong to that parent.

**Page links.** `"link": "<sheet name>"` on a box (any shape, `offpage` included) or a lane makes a
click on it open that sheet of the same file (in `colorful`, a lane's link sits on its title cell):
the cell is written inside draw.io's `UserObject` with
`link="data:page/id,<the sheet's diagram id>"`. Link an `offpage` box up to its parent's sheet, and
a parent box down to the sheet that details it. A name that is not a sheet of the file (the spec's,
or for `add` also the existing file's) stops `build` and `add`; `validate` reports a page link whose
target sheet is missing. A link adds no vertex to the ladder count and no colour.

## Legend

`{"type": "legend", "x": 700, "y": 585, "w": 330, "labels": {"human": "Max decides"}}` draws a frame
headed "Legend" with one row per look used by the boxes on that sheet: roles first, then catalogue
`shape`s, then frames (`minimal` and `bw`: one row, "group of parts" unless `labels.frame` says
otherwise; `colorful` lists frames by their role's colour), then the accent if any box or edge sets
it. Each
row is one cell: a sample drawn with the shape, paint and outline the boxes finally have (overrides
and a `shape=` in `style` included; colours compared after normalising), with its meaning beside
it. The outline is every value that changes how the border is drawn: `rounded`, `arcSize`,
`absoluteArcSize`, `double`, `dashed`, `dashPattern`, `strokeWidth`, `size`, `fixedSize`, `flipH`,
`flipV`, `direction` and `backgroundOutline`. In `minimal` and `bw` they are read from the box's
drawn style; in `colorful`, where a role row is a colour swatch, from its raw `style`. A value equal
to the shape's own (draw.io's default, or the catalogue's: `strokeWidth=1`, `size=8` on `predefined`)
is the same as none, and numbers compare as numbers (`1` and `1.0`). Rows are grouped by what is
finally drawn (the draw.io shape, these outline values and the paint, after every override), not by
the role or catalogue name behind it: a `plain` box whose `style` gives it the corners of a
`propose` box shares the `propose` row. So two boxes drawn alike share a row, and two roles drawn alike share one
row with both meanings and warn. A `rotation` turns the box but not its outline and is not shown. The accent sample is a box outline, or a line
when only edges carry the accent, drawn with the same colour and width as the accented cells. Two
meanings that share one look become one row showing both ("audit evidence; customer input"); boxes
of one role drawn two ways (a different paint or outline) give two rows and a warning. Default meanings: `human` a person decides, `propose` an LLM or skill proposes, `decide`
code decides, `guard` policy layer, `outside` outside system, `record` record or artifact, `note`
note, `stage` stage, `plain` component, `accent` highlighted; `labels` overrides any of them (keys are
role names, shape names or `accent`). Name the highlighted path in `labels.accent`. A `labels` key
that is none of these warns with the nearest valid name and is ignored; a key for a role, shape or
accent the sheet does not show warns too, since its row does not appear.

The title wraps at the legend's inner width (explicit line breaks included) and the rows start
below all of its lines. The title and row heights follow `font` and the wrapped length of each meaning, so the height is
known only after `build`, which prints the legend's box (`legend: sheet ... at x= y= w= h=`); about
26 px per row at font 9. A `w` that leaves under 40 px for the meanings, or 4 × font when that is
larger (48 px at font 12), stops the run. Two legends on one sheet each print their own `labels`. Legend cells
carry the style key `dsLegend=1` and are left out of the ladder count by `build`, `list` and
`validate`, which print them separately. In `minimal` and `bw` the legend frame is a container and
its rows are its children, so it moves as one unit in draw.io. Every sheet from L1 down gets a
legend; on L0 it is optional. In `minimal` and `bw`, `build` and `add` warn on an L1 to L4 sheet
without one (so `--strict` fails), and `validate --ladder` warns on such a sheet in an existing file.

## Commands

```
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio          # new file; an existing OUT stops the run
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio --force  # rebuild a file you built from this spec
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio --theme bw   # --theme minimal|colorful|bw overrides the spec
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio --max-colours 3   # minimal: 3 or 4 colours (or --max-colors)
python scripts/drawio_spec.py build SPEC.json -o OUT.drawio --strict    # exit 1 when any warning was printed (the file is still written)
python scripts/drawio_spec.py add SPEC.json --into FILE.drawio --position first|last|before:NAME|after:NAME   # keeps the file's theme and accent; --strict: a warned add leaves the file unchanged
python scripts/drawio_spec.py list FILE.drawio                       # theme, colours, palette breaches, then vertices / legend / edges per sheet
python scripts/drawio_spec.py validate FILE.drawio --ladder           # recorded palette, ids, dangling edges, page links, sizes, page bounds, level budgets, legends
python scripts/drawio_spec.py validate FILE.drawio --ladder --strict  # also exit 1 when any warn line was printed (the machine check after add)
python scripts/drawio_spec.py export FILE.drawio [--format png|svg|pdf] [--out DIR] [--scale 1.5]   # only if a draw.io CLI is installed
```

A spec without sheets (no `"sheets"` list, or an empty one) stops `build` and `add` before anything
is backed up or written; `validate` reports a file that holds no sheet as an `ERR`, and `list` warns.
`validate` exits 1 on any `ERR`; with `--strict` it also exits 1 when it printed any `warn` line (a
cell outside the page, a child in a lane's title strip, a missing legend, a name without an `L<n>`
prefix), so a file can be gated after `add` as a spec is with `build --strict`.

`build` refuses to overwrite an existing file: rebuilding a file you built from this spec takes
`--force`; an existing file you did not build takes new sheets through `add`. `build` and `add` end
with the line `warnings: N`. `add` without `--position` inserts the new sheets first, which suits a
drill-up only, so always pass it (`last` for a drill-down). `add` never rewrites an existing sheet:
a name or id collision stops the run, and so do two new sheets with one name, in every theme,
before any backup or write. Two sheets with one name in one spec also stop `build` in `minimal` and
`bw`, and in `colorful` when any cell has a `link`; a standalone `colorful` build without links
writes them, as the script did before themes. `validate` reports an existing file in which two
sheets share a name or a diagram id, since `before:NAME`, `after:NAME`, a link by name and a page
link by id could not tell them apart; `add` stops, before any backup or write, when its
`--position` or a `link` names such a sheet. `add` writes in one step: the new file goes to a
temporary file beside the destination, is read back, then `FILE.drawio.bak` is written and the
temporary file renamed over the destination, so a failed write leaves the original bytes in place.
`export` writes one file per sheet: `--format` png (default), svg or pdf; `--out` the folder
(default: the file's folder); `--scale` 1.5 by default. Each file is `<file>__<sheet>.<format>`, the
sheet name reduced to letters, digits, `_`, `.` and `-`; every name is resolved before the renderer
runs, and names that would collide (compared without regard to case: `L4 Review A` and
`L4 Review_A`, or two Korean titles) get the page number appended (`_p2`). The renderer's page
numbering (draw.io counts from 1 since 27.0.2; an older one from 0) is found once per run, on the
last page, and used for every page. Compressed sheets (draw.io's default save
format) are read for `list` and `validate` and left untouched by `add`. Cells that draw.io wrapped in an `object` or `UserObject` (a link, a
tooltip, custom data) are read through the wrapper: the id and label from it, the style, parent and
geometry from the cell inside.

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
  "Shapes" table; a share of the height divides instead: h ≥ (lines × 1.35 × font + 12) / (1 − share)),
  rounded up to a whole pixel (one line at font 9: 24.15, so 25 px).
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
  counting every rendered line of the frame's own text, wraps included, rounded up to a whole pixel
  (one line at font 9: 25 px),
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
together, so `"h": "auto"` and the same height given as a number draw and check the same; when the
inset leaves less than one character of width as the box grows, or the height does not settle,
`build` stops and asks for a wider box. The
warning names the height the box needs. Shorten the text or enlarge the box (or use `"h": "auto"`);
do not drop the font below 8. Text cells and lane titles are checked too: a text cell warns when its
wrapped text needs more than its `h`, a lane title when it does not fit its title strip (see
"Lanes"). The check, `"h": "auto"` and a frame's headline room measure what draw.io will draw: the
cell's `font` and `bold`, replaced by a `fontSize` or `fontStyle` in the box's `style` when it sets
one, and the shape's spacing, replaced by a `spacing`, `spacingLeft`, `spacingRight`, `spacingTop`
or `spacingBottom` in the `style`. The same font given as `"font": 14` or as `fontSize=14;` gives
the same height and the same warning. The text's width is the one draw.io leaves: a `labelWidth`
in the `style` when set; otherwise the shape's text width less its bars, slants or fold, taken from
the effective `size` and `fixedSize` (a `size=42` on `predefined` takes 42 px a side instead of 8),
and a `size` on `document`, `offpage`, `manual_input` or `database` changes its top or bottom
allowance the same way. When no width is left for the text, `build` warns. The width estimate is
approximate: check the PNG.

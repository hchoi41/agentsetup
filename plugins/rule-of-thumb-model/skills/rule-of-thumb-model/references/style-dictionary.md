# Style dictionary

- The file: `references/style_dictionary.json`. The scripts read it; this page explains it.
- Every cell of a model gets its look from one entry of this file, chosen by the cell's type (spec 2.2 P6). No cell is styled by hand.
- `rotm.py build` writes the look into every cell it makes. `rotm.py theme` writes it into a workbook made some other way. `rotm.py audit` compares the font name, size, font colour, bold, fill and number format of each cell with it; borders and alignment are not audited (section 5).
- Every value in the file carries a `source`. Section 10 lists every value with its source; it is generated from the JSON file, so the two cannot differ (a test checks this).
- A project changes the look with its own file `<model>/style_overrides.json` (section 4). Nobody edits `style_dictionary.json` for one project.

## 1. Max's fixed points

Max's words (spec Part 1, section 1.2), quoted:

> i usually prefer using Arial 8 or Arial 9 (fontsize)
> - 8 is for wheen there's lots of data (lots of data meaning a brick wall of data then 8)
> - there are some white spaces then 9
>
> i like minimal color usages, and i usually use light shade gray for the column headers and slightly less shade gray for row headers.

What the dictionary takes from them:

- Font: Arial in every cell type.
- Size: 8 on sheets that are a brick wall of data (raw, helpers, calc); 9 on sheets with white space (cover, inputs, output, checks, notes). A specification can set 8 or 9 for one sheet; any other size is refused.
- Sheet titles are 9 on every sheet, also on a size-8 sheet.
- Column headers: fill D9D9D9 (light grey).
- Row headers: fill F2F2F2, a lighter grey than the column headers.
- Minimal colour: the default palette has 7 colours: 000000 text, D9D9D9 and F2F2F2 header greys, FFF2CC input fill, 595959 note text, 808080 header rule, FF0000 for a failing check (FAIL) or a raised alert (ALERT) only.
- The two hex values of the greys are the plugin's choice within Max's description (research synthesis v2, decision D18). Max named no hex value.

These are fixed points. They are not claims to check against the research.

## 2. How to read the sources

- A `source` starts with a key of the `sources` table (section 10): `MAX`, `SYNTHESIS`, `SURVEY`, `R2`, `FAST`, `ICAEW`, `BOOKS`, `GUIDES`, `PLATFORMS`, `PLUGIN`.
- `SYNTHESIS D8` names a decision of the research synthesis v2. D8 is the style dictionary, D9 number formats, D13 sheet view, D16 input marking, D17 link colour, D18 greys, density and the unit band.
- `check #11`, `check C2.2`, `check L-50`, `check B-17` name one item of the item-by-item claim checks in `research/verify/*_check_v1.json`. Those items were read again in the primary source and confirmed.
- `partly` after a check id: the check confirmed the figures but not the whole claim.
- `not re-checked`: the claim is in a research report but is not among the checked items. It is not confirmed. It is used only where no confirmed claim says otherwise, and the value is a default that can be overridden.
- `PLUGIN`: the plugin's own choice. Where it matters to Max it is listed in section 3.
- FAST Standard 02c rule numbers are cited from the platforms research (FAST Standard Organisation, licensed CC BY 4.0).
- The ICAEW Financial Modelling Code is cited in the plugin's own words, not quoted.

## 3. Choices for Max

Where the research shows two conventions, the dictionary uses one by default and keeps the other as a named override. Neither is described as wrong. Max picks.

| Question | Default (and its source) | Alternative, one switch (and its source) |
| --- | --- | --- |
| How inputs are marked | black text on fill FFF2CC: Max's own files mark inputs on a light yellow or amber fill ("Blue font on amber fill", Variance_FlashReport Cover B7, read again by M2 from the copy in `build/foreign`; "파란 숫자와 옅은 노란색 셀", GUIDES C-4); ICAEW asks for a fill or border, not font colour alone (check C2.2); design D7 | `input_blue_font`: blue 0000FF text, no fill. This is the synthesis's recommended default (SYNTHESIS D16 option A: one colour, fewest colours); Kaplan 0000FF on 86.4% of 2,374 typed numbers (check #7); CFI 3271D2 or 0000FF on 87.2% of 9,063 (check #8); CFI p.83 (check L-50) |
| How inputs are marked (second alternative) | as above | `input_blue_on_fill`: blue 0000FF text on FFF2CC, the look Max's own legend describes (GUIDES C-1, C-4); IFM p209 and FMD p51 (check SecB rows); Wall Street Prep (check C3.2) |
| Colour of references to other sheets | black, as calculations: 75.8% of 86,899 pure links are black (check #11); SYNTHESIS D17 | `links_green`: green 008000, as in Max's own legend ("Green font" = cross-sheet link, GUIDES C-1) and his prior work (38.9% of 522 links, check #12); CFI (check L-50) |
| FAST colours for imports and exports | not used: blue would collide with `input_blue_font`, red marks failing checks (SYNTHESIS D17) | `fast_import_export`: importing cells blue 0000FF (FAST 1.01-06, check C1.9). In a built model it colours the importing cells only: build gives no cell the export type, so the cells they read stay black. Exports turn red FF0000 only when theme styles a workbook with no manifest (section 6, formulas) |
| Grey on the unit column | F2F2F2, the row-header grey, so the row band is continuous (design 10.2) | `unit_unfilled`: no fill; Max named grey for column and row headers only (SYNTHESIS D18) |
| Colour of the top rule on totals | black 000000 (design 10.2); top border preferred so rows can be inserted above (GUIDES L-68, not re-checked) | `total_grey_rule`: grey 808080 (SYNTHESIS D8) |
| The two grey hex values | D9D9D9 and F2F2F2 (SYNTHESIS D18: both the survey and researcher 2 propose D9D9D9; F2F2F2 is the lighter of the survey's F2F2F2 and researcher 2's EEEEEE) | any other greys, through `style_overrides.json` (section 4) |
| When a sheet the plugin did not build counts as dense | at least 60% of its used block filled and at least 500 filled cells (SYNTHESIS D18, builder default) | `rules.dense_fill_ratio`, `rules.dense_min_cells` in `style_overrides.json` |

Related rule values in the same file (they are explained in the main SKILL.md): `depth_limit` 2 (Max, spec 1.2); `length_limit` 100 characters (the plugin's own number); `allowed_literals` 0 and 1; `check_tolerance` 0.001 in the unit of the checked values; `helper_spare_rows` 0 rows; `formula_key_check_max_rows` 5,000 rows; `sparse_sheet_cells` 100 populated cells (build's information note I-SPARSE-SHEET for a calculation sheet that takes size 8 from its role; build does not change the size).

## 4. A project's own override

- File: `<model>/style_overrides.json`, or the file the specification names in `"dictionary"` (a path relative to the model folder).
- Order of layers: the plugin dictionary; then named overrides given on the command line (`--use NAME`); then the override file's own `use` list; then its `set` part, merged key by key.
- A leaf in `set` replaces the old leaf whole. A border given in `set` replaces the whole border.
- Every leaf in `set` needs its own `source`. A leaf without one is refused, with its path.
- A colour key, number-format key or named override that does not exist is refused, with the nearest name ("did you mean ...").
- The applied layers are listed in the manifest (`dictionary_applied`) and on the cover's legend.

Example: inputs in blue font without fill, and the column-header grey of a client's house style.

```json
{
 "rotm_dictionary_override": 1,
 "use": ["input_blue_font"],
 "set": {
  "colors": {"col_header_fill": {"value": "D0CECE", "source": "project: client house style, email of 2026-11-20"}}
 }
}
```

## 5. How the commands use it

- build: every cell gets `styles.apply(cell, dictionary, cell_type, size=..., format_key=...)`. One named style per cell type is registered (`rotm input`, `rotm calc`, ...); size and number format are then set on each cell. The manifest records each range's cell type.
- build, checks sheet: the Result cell of a check of severity error has the cell type `check` (number format `check`: 0 shows OK, 1 shows FAIL in red). The Result cell of an alert row has the cell type `alert` (number format `alert`: 0 shows OK, 1 shows ALERT in red), so a raised alert never shows FAIL. The two cell types differ only in the number format. The audit's S-NUMFMT rule reads the cell type from the manifest and expects the matching format.
- build, raw columns: the number format is the contract column's `format`; without one, `contract_type_formats` gives it by type, except that a `number` column whose landed values hold a fraction gets `amount2` (two decimals, BOOKS B-44), or `general` when a value has more than two decimals, so landed values are not shown rounded (7.25 as 7, live test 1 on 2026-11-25; the plugin's rule). The manifest records each raw column's format in `formats`, and the audit's S-NUMFMT rule compares with it.
- build, helper fetch columns: a fetch column gets the format of its raw column, except that the fetch of a `date` or `datetime` column that the contract allows to be empty (null rule `allowed`, or agreed known nulls) gets `date_or_empty` or `datetime_or_empty`. Their zero section is empty, so the 0 that the fetch returns for an empty raw cell shows nothing instead of 1900-01-00. The fetched value is still 0; a calculation that must tell an empty date from a date reads the null flag column `<column>_null`. The manifest records the key as the helper column's `format`, and `rotm.py extend` writes it into new rows.
- theme: classifies every non-empty cell (section 6), then sets the font name, size, colour, bold, italic and fill. `--number-formats`, `--borders`, `--align` and `--views` add number formats, borders, alignment and wrap, and gridlines and zoom (section 7).
- audit: the S-* rules compare six attributes of each non-empty cell with the dictionary: font name (S-FONT), size (S-SIZE), font colour (S-FONTCOLOR), bold (S-BOLD), fill (S-FILL) and number format (S-NUMFMT).
  - With a manifest, each cell is compared with the look of its cell type: font name, size, font colour and fill on every cell the manifest types; bold on column headers, titles, headings, totals, outputs and the master check.
  - S-NUMFMT compares the number format of these cells with the dictionary: the cells whose cell type fixes a format (not `item`), the raw columns (the format key build recorded for the column in the manifest, `formats`, else the column type's default), the scalar inputs (their format key) and the header cells that show an item of an input list. It does not compare the format of block cells or helper cells: the manifest records no format key for a block cell, and a helper cell has the cell type `calc`, whose format is the item's.
  - A header cell that shows an item of an input list is compared with the format build gives it (the block's rows or columns format, else the list's format, else date for row headers or month for column headers when the item is an ISO date, else General); a header of a list whose format is text may hold the text format or General.
  - Without a manifest, the cell types are not known: the font must be the dictionary's font, the size 8 or 9, and the fill and the font colour must be colours of the dictionary's palette. Bold and number formats are not compared.
- borders and alignment: not audited. build writes them into every cell it makes, and theme writes them with `--borders` and `--align`; a border or an alignment changed by hand afterwards gives no finding. Italic, wrap and locking are not audited either.
- Equivalences used in every comparison (`lib/styles.py`):
  - An automatic font colour counts as 000000. Excel draws automatic text in black.
  - Font names compare without regard to case.
  - Sizes compare within 0.01 point.
  - General alignment counts as right for numbers, dates and booleans and as left for text, because that is what Excel shows.
  - Centre across selection counts as centre.
  - A white fill FFFFFF is a fill. It is not "no fill".
  - Number formats (`lib/styles.py` `number_formats_equal`, used by audit S-NUMFMT and by theme when it plans, when it reads its result again (T-RESIDUAL) and with `--number-formats`): letter case does not count, and a literal character is the same whether it is written bare, after a backslash or in quotes (Excel saves `yyyy-mm-dd` as `yyyy\-mm\-dd`). A character that has a meaning of its own when bare (the percent sign, which multiplies by 100; the digit placeholders `0`, `#` and `?`; a comma that scales; the date and time letters) is not the same as that character in quotes or after a backslash: `0.0%` and `0.0"%"` differ.

## 6. How a workbook the plugin did not build is read

`lib/classify.py` gives each non-empty cell a cell type. It reads values, formulas and positions only, never styles. theme changes styles only, so the audit afterwards infers the same types and sizes that theme used. A test changes every font and fill of a workbook at random and checks that the types do not change.

Sheets:

- A sheet is raw when the manifest says so, when its name matches `^(raw|data|src|landed)` or ends in `_raw` (case ignored), when a table on it is loaded by a query, or when `--raw-sheets` names it. On a raw sheet the first non-empty row is `col_header`; every other cell is `raw`. Raw sheets are size 8.
- A sheet named `h_...` or `helper...` is a helper sheet, size 8.
- Any other sheet is dense, size 8, when at least 60% of its used block holds a value and it holds at least 500 filled cells (Max: "a brick wall of data then 8"). The used block runs from the first to the last used row and column.
- Otherwise it is size 9, and its type comes from its name: check, cover or contents, input or assumption, notes, output or summary; else output.
- With a manifest, the manifest's sheet type and size win.

Cells on other sheets, in this order:

1. `col_header`: text, a date or a year (1990 to 2100) found above the first cell of a run of numbers or formulas in the same column, at most 6 rows up, when the run has 2 or more cells or the row has 2 or more such headers. A header row is extended to header-like cells beside it, across gaps of at most 2 empty columns. Formulas that show text or dates count (a timeline row of `=EOMONTH(...)`).
2. `check` and `master_check`: formulas right of a label containing check, chk, control, error, diff, reconcil, 검증, 체크, 차이 or 오류 (not "balance sheet"); the first formula right of "master", "overall", "global", "model" or "total" check is `master_check`. Also a formula with an OK, ERROR, FAIL or PASS text result, and a comparison on a sheet named check.
3. `row_header`: text left of the first value of a row (also a formula that shows text there, such as a link to a list label). `unit` instead when the column's header says unit, units, uom or 단위.
4. `note`: typed text right of the last value of a row, text of 40 or more characters, and text starting with note, source, 출처, 참고 or ※.
5. `title`: the first typed text in rows 1 to 3. `heading`: short typed text alone in its row with values or headers within 2 rows below. `label`: other typed text.
6. Formulas: `link` (one reference to the same sheet and nothing else), `import` (one reference to another sheet or workbook), `total` (in a row labelled total or in a column headed total), `export` (read by a formula on another sheet), else `calc`.
7. `input`: typed numbers, dates and booleans.
8. `output` is never inferred: which results are key is not visible in the cells. Build marks them from the specification.

## 7. theme: what it changes and what it never changes

- Changes: the look of non-empty cells (font name, size, colour, bold, italic, fill; with flags, number format, borders, alignment and wrap, gridlines and zoom).
- Never changes: values, formulas, sheets, sheet order, column widths, row heights, merged cells, frozen panes, conditional formats, data validations, defined names, tables, table styles, charts, Power Query queries, pivot tables.
- A check cell gets the check number format only when it holds 0 or 1 (finding `T-NUMFMT-SKIPPED` lists the others).
- Writer: openpyxl only for an `.xlsx` with no part openpyxl would drop. A workbook with such a part (Power Query, charts, drawings, pivot tables, form controls, rich text, macros, x14 extensions) is edited through Excel. Without Excel such a workbook is refused (exit 3, `T-FRAGILE`) and nothing is written.
- Comparison by code, before anything is written: every formula and typed value, and the counts of sheets, tables, defined names, charts, conditional formats, data validations, merged ranges, pivot tables and queries. With Excel (default when Excel is available) copies of both files are recalculated first and every value is compared too. Any difference is an error (`T-UNINTENDED`) and nothing is written.
- Excel merges duplicate conditional-format rules when it opens a file. On a copy of a foreign workbook openpyxl counted 23 rules in the original, Excel 19, and a plain Excel re-save also held 19 (2026-11-25). theme therefore settles a count difference of conditional formats or validations with Excel's own count of both files.
- After writing, the result is read again and compared with the dictionary; a cell that still differs is a warning (`T-RESIDUAL`).
- Output: `<stem>_theme.xlsx` next to the workbook, or `-o FILE`. `--in-place` replaces the workbook after keeping `<stem>.bak_<YYYYMMDD_HHMMSS>.xlsx`.
- In a model folder: the manifest's dictionary fields and `reports/status.json` are updated.
- Exit codes: 0 done; 1 an unintended change, or with `--strict` any warning; 2 bad input; 3 Excel needed and not available; 5 the Excel lock was busy.

## 8. Verified and not verified

Verified on this PC (2026-11-25, Windows 11, Excel 16.0, openpyxl 3.1.5):

- The dictionary loads, and every source names a key of `sources` (`dictionary.source_problems` returns nothing).
- A cell styled by `styles.apply` and read back from the saved file matches the dictionary in every attribute, for every cell type at size 8 and 9.
- theme on an openpyxl-made workbook: every formula and typed value, the defined names, tables, conditional formats, data validations, merged cells, frozen panes and column widths are equal before and after (read by openpyxl, independently of theme's own comparison).
- theme followed by the style check: no cell differs in font, size, colour, bold or fill.
- theme in strict mode through Excel on copies of the 9 readable workbooks in `build/foreign` (Power Query with a pivot and a Data Model, charts, form controls, rich text, x14 extensions; 40,244 non-empty cells): exit 0 for all 9; every formula and value equal after recalculation in Excel; every formula and typed value equal again when read by openpyxl; no cell left off the dictionary in font, size, colour, bold or fill; none of the 34 Excel processes the jobs recorded was still running afterwards.
- The other 2 copies were refused with a fix (exit 2): an old binary `.xls` and a password-protected `.xlsm`.

Not verified:

- How Google Sheets shows the dictionary after an `.xlsx` is converted: Arial, the fills, the `[Red]` part of the check format, the `_)` padding of the amount format (no Google account for the workers; platforms E5).
- How Korean text in an Arial cell looks: Arial has no Hangul glyphs, so Excel and Google Sheets draw it in a fallback font (SYNTHESIS D8; general product behaviour, not checked here).
- Gridlines and zoom through Excel: the Excel bridge has no view operation, so `--views` sets them only on the openpyxl path (`T-VIEWS` warning otherwise).
- The density thresholds (60%, 500 cells) are a builder default, not calibrated against Max's own judgment of which sheets are a brick wall.

## 9. Common mistakes

- Styling a cell by hand in Excel. The next audit reports it; run `rotm.py theme` or rebuild.
- Editing `style_dictionary.json` for one project. Use `<model>/style_overrides.json`.
- An override leaf without a `source`. It is refused.
- Expecting theme to remove conditional formats or table styles. It reports them (`T-CF`, `T-TABLESTYLE`) and leaves them.
- Running theme on a workbook with a Power Query or a chart on a PC without Excel. It refuses and writes nothing; run it where Excel is installed.
- Reading the `_theme.xlsx` of an openpyxl-path run with a data reader before Excel has opened it, on a PC without Excel: openpyxl does not store calculated values, so the reader sees no values. With Excel present, theme delivers a recalculated file.

## 10. Every value and its source

<!-- generated from style_dictionary.json by lib.styles.dictionary_markdown: do not edit by hand -->

### Sources

| Key | What it is |
| --- | --- |
| MAX | Max's stated preferences (spec Part 1, section 1.2): Arial; size 8 for a brick wall of data, 9 where there is white space; minimal colour; light grey column headers; row headers a lighter grey. Fixed points, not claims to check. |
| SYNTHESIS | Research synthesis v2 (research/20261124_create_skill_ruleofthumb_financial_modeling_research_synthesis_v2.md), decisions D8 (style dictionary), D9 (number formats), D13 (sheet view), D16 (input marking), D17 (link colour), D18 (greys, density, unit band). |
| SURVEY | Style survey of the template files on Max's PC (research ..._research_style_survey_v1.md, sections C and F, pass 2); claim checks in research/verify/style_survey_check_v1.json. |
| R2 | Researcher 2 report, section 5 (research/researcher2/research2_report_v1.md); claim checks in research/verify/researcher2_check_v1.json. |
| FAST | FAST Standard 02c, July 2019, FAST Standard Organisation, licensed CC BY 4.0 (via research ..._platforms_standards_v1.md, section C1); claim checks in research/verify/platforms_standards_check_v1.json. |
| ICAEW | ICAEW Financial Modelling Code (2024), summarised in research ..._platforms_standards_v1.md, section C2 (all rights reserved; not quoted); claim checks in research/verify/platforms_standards_check_v1.json. |
| BOOKS | Book research (research ..._research_books_v1.md); claim checks in research/verify/books_check_v1.json. |
| GUIDES | Local guides research (research ..._research_local_guides_v1.md), including Max's own files (its section C); claim checks in research/verify/local_guides_check_v1.json. |
| PLATFORMS | Platform research (research ..._research_platforms_standards_v1.md); claim checks in research/verify/platforms_standards_check_v1.json. |
| PLUGIN | The plugin's own choice (design rotm_design_v1.md, or builder M2); listed for Max to decide where it matters. |

### Font and sizes

| Entry | Value | Source |
| --- | --- | --- |
| font | Arial | MAX (Arial in every cell); SYNTHESIS D8 (Arial has no Hangul glyphs, so Korean text is drawn in a fallback font; the stored font stays Arial) |
| sizes.dense | 8 | MAX (8 for a brick wall of data) |
| sizes.spaced | 9 | MAX (9 where there is white space) |

### Sheet types

| Sheet type | Size | Gridlines | Zoom (%) | Freeze at | Sources |
| --- | --- | --- | --- | --- | --- |
| cover | spaced (9) | hidden | 100 | none | size: MAX; SYNTHESIS D18; gridlines: SYNTHESIS D13; SURVEY F (hidden on 762 of 1,008 sheets, 75.6%, check #18); BOOKS B-40 (HE rule 92, check B-40); zoom: SYNTHESIS D13 (PLUGIN default; PwC suggests 85, GUIDES L-75); freeze: PLUGIN |
| inputs | spaced (9) | hidden | 100 | D4 | size: MAX; SYNTHESIS D18; judge plan approval seq 1 advice 5; gridlines: SYNTHESIS D13; SURVEY F (check #18); BOOKS B-40; zoom: SYNTHESIS D13; freeze: PLUGIN layout (design 6.3); FAST 2.03-05 (master check in the frozen area) |
| raw | dense (8) | hidden | 100 | A2 | size: MAX; SYNTHESIS D18 (raw sheets are always dense); gridlines: SYNTHESIS D13; SURVEY F (check #18); BOOKS B-40; zoom: SYNTHESIS D13; freeze: PLUGIN (header row frozen); SYNTHESIS D13 (raw sheets at A2) |
| helpers | dense (8) | hidden | 100 | C2 | size: MAX (a wide grid of helper columns); gridlines: SYNTHESIS D13; SURVEY F (check #18); BOOKS B-40; zoom: SYNTHESIS D13; freeze: PLUGIN (header row, row and in_raw columns frozen) |
| calc | dense (8) | hidden | 100 | D4 | size: MAX (wide grids); judge plan approval seq 1 advice 5; gridlines: SYNTHESIS D13; SURVEY F (check #18); BOOKS B-40; zoom: SYNTHESIS D13; freeze: PLUGIN layout (design 6.3); FAST 2.03-05 |
| output | spaced (9) | hidden | 100 | D4 | size: MAX (summaries); judge plan approval seq 1 advice 5; gridlines: SYNTHESIS D13; SURVEY F (check #18); BOOKS B-40; zoom: SYNTHESIS D13; freeze: PLUGIN layout (design 6.3); FAST 2.03-05 |
| checks | spaced (9) | hidden | 100 | D5 | size: MAX; gridlines: SYNTHESIS D13; SURVEY F (check #18); BOOKS B-40; zoom: SYNTHESIS D13; freeze: PLUGIN layout (design 9.1: header row 4) |
| notes | spaced (9) | hidden | 100 | none | size: MAX (notes); judge plan approval seq 1 advice 5; gridlines: SYNTHESIS D13; SURVEY F (check #18); BOOKS B-40; zoom: SYNTHESIS D13; freeze: PLUGIN |

### Colours

| Key | Hex | Source |
| --- | --- | --- |
| text | 000000 | SYNTHESIS D8; SURVEY C and F (82.0% of 273,804 formula cells black, check #11); R2 section 5 (109,820 of 123,561 calculation cells black or automatic, check #13) |
| col_header_fill | D9D9D9 | MAX (light grey column headers); the hex D9D9D9 is the PLUGIN's pick within Max's description, as in SYNTHESIS D18 (SURVEY F and R2 section 5 both propose D9D9D9); for Max to confirm |
| row_header_fill | F2F2F2 | MAX (row headers a lighter grey than the column headers); hex F2F2F2 from SURVEY F, the lighter of SURVEY F2F2F2 and R2 EEEEEE (SYNTHESIS D18); for Max to confirm |
| input_fill | FFF2CC | GUIDES C-1 and C-4 (Max's own files mark inputs on a fill: 'Blue font on amber fill', '파란 숫자와 옅은 노란색 셀'; C-1 read again by M2 from the copy in build/foreign, Cover B7, and FFF2CC is the fill on its Assumptions C23:E23); SURVEY pass 2 (FFF2CC on 13.4% of 1,516 typed numbers in Max's prior work; the figure is read in check #10); ICAEW (a fill and/or border, not font colour alone; check C2.2); SYNTHESIS D16 option B |
| rule_line | 808080 | SYNTHESIS D8 (thin 808080 rules, builder default); GUIDES L-69 (grey rather than black borders; not re-checked) |
| note_text | 595959 | SYNTHESIS D8 (regular 595959 notes; section 3 row 21); R2 section 5 (dark grey notes, a proposal) |
| fail_text | FF0000 | PLUGIN: the [Red] colour of the check number format, used only for failing checks (design 9.5); SYNTHESIS D8 (FF0000 for failing checks) |
| input_blue | 0000FF | SYNTHESIS D16 option A; SURVEY F (Kaplan: 0000FF on 86.4% of 2,374 typed numbers, check #7; CFI: 3271D2 on 62.8% and 0000FF on 24.4% of 9,063, check #8); GUIDES L-50 (CFI p.83, check L-50) |
| link_green | 008000 | SYNTHESIS D17 option; SURVEY pass 2 (Max's prior work: 008000 on 38.9% of 522 pure links, check #12); GUIDES L-50 (green for links to other sheets, check L-50); GUIDES C-1 (Max's legend: 'Green font' = cross-sheet link) |
| export_red | FF0000 | FAST 1.01-06 (red font for exports, check C1.9) |

### Number formats

| Key | Format | Source |
| --- | --- | --- |
| amount | `#,##0_);(#,##0);"-"_)` | SYNTHESIS D9; GUIDES L-64 (negatives in brackets, zero as a dash, no red for negatives: PwC p.13, CFI p.81, check L-64); BOOKS B-44 (check B-44); SURVEY F |
| amount1 | `#,##0.0_);(#,##0.0);"-"_)` | SYNTHESIS D9 (amount_1dp: Kaplan's one-decimal format, SURVEY B) |
| amount2 | `#,##0.00_);(#,##0.00);"-"_)` | BOOKS B-44 (HE: two decimals as a general rule, check B-44) |
| rate | `0.0%` | SYNTHESIS D9; SURVEY F; R2 section 5; BOOKS B-44 (percent cells in percent format) |
| rate2 | `0.00%` | PLUGIN |
| factor | `0.0000` | SYNTHESIS D9; FAST 3.01-08 (format numbers to show what they are; not re-checked) |
| count | `#,##0` | SYNTHESIS D9 (builder default); R2 section 5 (a zero stays visible for counts) |
| integer | `0` | PLUGIN |
| date | `yyyy-mm-dd` | SYNTHESIS D9; R2 section 5 |
| datetime | `yyyy-mm-dd hh:mm:ss` | PLUGIN |
| date_or_empty | `yyyy-mm-dd;;` | PLUGIN (r5 verdict J-1: an empty date shows as an empty cell, not 1900-01-00) |
| datetime_or_empty | `yyyy-mm-dd hh:mm:ss;;` | PLUGIN (r5 verdict J-1: an empty date shows as an empty cell, not 1900-01-00) |
| month | `yyyy-mm` | SYNTHESIS D9 (period headers yyyy-mm, builder default) |
| text | `@` | SYNTHESIS D9; R2 section 5 (identifiers with leading zeros stay text) |
| flag | `0` | SYNTHESIS D9 (0/1 flags and exception counts, so a zero stays visible); R2 section 5 |
| check | `[Red]"FAIL";[Red]"FAIL";"OK"` | PLUGIN (design 9.5): 0 shows OK, 1 shows FAIL in red; MAX (minimal colour: no fill, no conditional format) |
| alert | `[Red]"ALERT";[Red]"ALERT";"OK"` | PLUGIN (v4, J-1): the Result cell of an alert row: 0 shows OK, 1 shows ALERT in red; an alert is not a failure and is not part of the master check; MAX (minimal colour: no fill, no conditional format) |
| general | `General` | PLUGIN (Excel's default format); SYNTHESIS D9 (labels and notes) |

Contract column types take these formats (design 6.8, land and build):

| Contract type | Format key | Source |
| --- | --- | --- |
| text | text | PLUGIN; SYNTHESIS D9 (IDs as @) |
| integer | count | PLUGIN (integrator, render review: whole-number KRW amounts proposed as integer showed 145754000 under format 0); SYNTHESIS D9 (count #,##0) |
| number | amount | PLUGIN; SYNTHESIS D9 |
| date | date | PLUGIN; SYNTHESIS D9 |
| datetime | datetime | PLUGIN |
| boolean | general | PLUGIN |

### Cell types

#### input

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a typed value a user may change (FAST Appendix B: a cell without cell references) | FAST |
| font_color | `text` (000000) | PLUGIN default (design D7, 10.3); alternatives: input_blue_font (SYNTHESIS D16 option A, its recommended default) and input_blue_on_fill (SYNTHESIS D16 option B); for Max to decide |
| bold | no | R2 section 5 (24 of 26 input specimens regular, check #12); SYNTHESIS D8 |
| italic | no | PLUGIN |
| fill | `input_fill` (FFF2CC) | GUIDES C-1, C-4 (Max's own files mark inputs on a light yellow or amber fill); ICAEW (check C2.2); SYNTHESIS D16 option B; see colors.input_fill |
| border | none | SYNTHESIS D8 (none, builder default); R2 section 5 (15 of 26 input specimens without borders) |
| number_format | `item` | PLUGIN (the item's format); SYNTHESIS D9 (by unit) |
| align | `right` | SYNTHESIS D8; GUIDES L-67 (right-aligned figures; not re-checked) |
| wrap | no | PLUGIN |
| locked | no | GUIDES L-60 (lock everything except inputs; not re-checked); SYNTHESIS D13 (no sheet protection by default) |
| size | the sheet's size (8 or 9) | MAX |

#### calc

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a formula that calculates | FAST |
| font_color | `text` (000000) | SYNTHESIS D8; SURVEY F (82.0% black, check #11) |
| bold | no | SYNTHESIS D8; SURVEY C (88.8% of 273,804 formulas not bold) |
| italic | no | SURVEY C |
| fill | none | SYNTHESIS D8; SURVEY F (79.0% unfilled, check #11) |
| border | none | SYNTHESIS D8; SURVEY C (52.3% no border) |
| number_format | `item` | PLUGIN; SYNTHESIS D9 |
| align | `right` | SYNTHESIS D8; GUIDES L-67 (not re-checked) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### link

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a pure reference to a cell on the same sheet (FAST Appendix B: a link has no functions or operators) | FAST |
| font_color | `text` (000000) | SYNTHESIS D17; SURVEY F (75.8% of 86,899 pure links black, check #11) |
| bold | no | SURVEY C |
| italic | no | SURVEY C |
| fill | none | SYNTHESIS D8; SURVEY F (82.5% unfilled, check #11) |
| border | none | SURVEY C |
| number_format | `item` | PLUGIN |
| align | `right` | SYNTHESIS D8; GUIDES L-67 (not re-checked) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### import

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a pure reference to a cell on another sheet (FAST 1.01-06 import) | FAST |
| font_color | `text` (000000) | SYNTHESIS D17 (black by default, as calculations; green is the named override links_green; FAST blue is fast_import_export) |
| bold | no | SURVEY C |
| italic | no | SURVEY C |
| fill | none | SYNTHESIS D8; SURVEY F |
| border | none | SURVEY C |
| number_format | `item` | PLUGIN |
| align | `right` | SYNTHESIS D8; GUIDES L-67 (not re-checked) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### export

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a calculation read by another sheet (FAST 1.01-06 export) | FAST |
| font_color | `text` (000000) | PLUGIN (looks like calc; FAST red is the named override fast_import_export) |
| bold | no | SURVEY C |
| italic | no | SURVEY C |
| fill | none | SYNTHESIS D8; SURVEY F |
| border | none | SURVEY C |
| number_format | `item` | PLUGIN |
| align | `right` | SYNTHESIS D8; GUIDES L-67 (not re-checked) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### check

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a check result: 0 when the check passes, 1 when it fails | GUIDES L-38 (check L-38) |
| font_color | `text` (000000) | PLUGIN (red only through the check number format, design 9.5) |
| bold | no | SURVEY E (99.1% of check formulas not bold; not re-checked) |
| italic | no | PLUGIN |
| fill | none | MAX (minimal colour); SYNTHESIS D8 (63.2% of check displays unfilled, SURVEY E) |
| border | none | PLUGIN |
| number_format | `check` (`[Red]"FAIL";[Red]"FAIL";"OK"`) | PLUGIN (design 9.5) |
| align | `center` | SYNTHESIS D8 (builder default) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### alert

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | the Result cell of an alert row: 0 when the alert is not raised, 1 when it is raised | PLUGIN (v4, J-1) |
| font_color | `text` (000000) | PLUGIN (red only through the check number format, design 9.5) |
| bold | no | SURVEY E (99.1% of check formulas not bold; not re-checked) |
| italic | no | PLUGIN |
| fill | none | MAX (minimal colour); SYNTHESIS D8 (63.2% of check displays unfilled, SURVEY E) |
| border | none | PLUGIN |
| number_format | `alert` (`[Red]"ALERT";[Red]"ALERT";"OK"`) | PLUGIN (v4, J-1): shows ALERT or OK, never FAIL |
| align | `center` | SYNTHESIS D8 (builder default) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### master_check

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | the master check on the checks sheet and its links on every block sheet | FAST 2.03-05; BOOKS B-34 (one master check shown on every sheet, check B-34) |
| font_color | `text` (000000) | PLUGIN (red only through the check number format) |
| bold | yes | SYNTHESIS D8 (check status bold, builder default) |
| italic | no | PLUGIN |
| fill | none | MAX (minimal colour) |
| border | none | PLUGIN |
| number_format | `check` (`[Red]"FAIL";[Red]"FAIL";"OK"`) | PLUGIN (design 9.5) |
| align | `center` | SYNTHESIS D8 (builder default) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### note

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | explanation or source text in a visible column (FAST 2.04-02) | FAST |
| font_color | `note_text` (595959) | SYNTHESIS D8 (595959); R2 section 5 |
| bold | no | SYNTHESIS D8; SURVEY C (88.0% of note-like text not bold; not re-checked) |
| italic | no | SYNTHESIS D8 (regular, section 3 row 21); R2 section 5 |
| fill | none | SYNTHESIS D8; SURVEY C (82.8% of 4,240 note-like cells unfilled; not re-checked) |
| border | none | PLUGIN |
| number_format | `general` (`General`) | PLUGIN; SYNTHESIS D8 |
| align | `left` | SYNTHESIS D8 |
| wrap | yes | SYNTHESIS D8 (left, wrap) |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### col_header

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a column header (also the header row of a raw sheet) | MAX |
| font_color | `text` (000000) | SYNTHESIS D8 (000000, builder default) |
| bold | yes | SYNTHESIS D8; SURVEY F (70.0% of 6,573 header cells bold, check #14) |
| italic | no | PLUGIN |
| fill | `col_header_fill` (D9D9D9) | MAX (light grey); SYNTHESIS D8, D18 |
| border | bottom thin 808080 | SYNTHESIS D8 (thin bottom 808080, builder default); R2 section 5 (thin grey bottom rule) |
| number_format | `item` | PLUGIN (General for text; period headers keep the column's date format, SYNTHESIS D9) |
| align | `center` | SYNTHESIS D8 (builder default) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### row_header

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | the label of a row of values | MAX |
| font_color | `text` (000000) | SYNTHESIS D8; SURVEY C (65.0% black) |
| bold | no | SYNTHESIS D8; SURVEY F (81.1% of 26,038 row-header cells not bold, check #15) |
| italic | no | PLUGIN |
| fill | `row_header_fill` (F2F2F2) | MAX (a lighter grey than the column headers); SYNTHESIS D8, D18 |
| border | none | SYNTHESIS D8 (builder default) |
| number_format | `general` (`General`) | PLUGIN; SYNTHESIS D8 |
| align | `left` | SYNTHESIS D8 |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### unit

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | the unit of a row (FAST 3.05 units designator) | FAST |
| font_color | `text` (000000) | PLUGIN |
| bold | no | SYNTHESIS D8 |
| italic | no | PLUGIN |
| fill | `row_header_fill` (F2F2F2) | PLUGIN (design 10.2: keeps the row-header band continuous); SYNTHESIS D18 leaves the unit column unfilled, kept as the named override unit_unfilled; for Max to decide |
| border | none | PLUGIN |
| number_format | `general` (`General`) | PLUGIN |
| align | `center` | SYNTHESIS D8 (builder default) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### raw

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a landed raw value (never typed or edited) | MAX (spec 1.2: data loaded on sheets as raw files; spec 2.2 P2) |
| font_color | `text` (000000) | SYNTHESIS D8; SURVEY C (raw-sheet cells automatic 59.4% or 000000 40.4%, check #20) |
| bold | no | SYNTHESIS D8 |
| italic | no | PLUGIN |
| fill | none | SYNTHESIS D8; SURVEY C (87.5% of 26,344 raw-sheet cells unfilled, check #20); BOOKS B-50 (PFM: no input colouring for databases; not re-checked) |
| border | none | SYNTHESIS D8; ICAEW (minimise formatting in data tables; not re-checked) |
| number_format | `item` | PLUGIN (from the contract type); SYNTHESIS D9 (contract format) |
| align | `type` | SYNTHESIS D8 (numbers right, text left) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX (8 on raw sheets) |

#### title

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | the sheet title in B1 | BOOKS B-41 (IFM: sheet title in A1, check B-41 partly confirmed) |
| font_color | `text` (000000) | SYNTHESIS D8; SURVEY C (52.1% black; not re-checked) |
| bold | yes | SYNTHESIS D8; SURVEY C (68.0% of 997 title cells bold; not re-checked) |
| italic | no | PLUGIN |
| fill | none | SYNTHESIS D8; SURVEY C (66.1% unfilled; not re-checked); MAX (minimal colour) |
| border | none | SYNTHESIS D8; SURVEY C (75.0% no border; not re-checked) |
| number_format | `general` (`General`) | PLUGIN |
| align | `left` | SYNTHESIS D8 |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | 9 | MAX (8 or 9 only); SYNTHESIS D8 (the title is 9 on every sheet; section 3 row 20) |

#### heading

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a block title (a section label inside a sheet) | PLUGIN |
| font_color | `text` (000000) | SYNTHESIS D8 |
| bold | yes | SYNTHESIS D8 (section labels bold, builder default; R2 section 5) |
| italic | no | PLUGIN |
| fill | none | MAX (minimal colour) |
| border | none | PLUGIN |
| number_format | `general` (`General`) | PLUGIN |
| align | `left` | SYNTHESIS D8 |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### total

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a block's display total (FAST 3.01-04); checks, and links from other blocks, may read it | FAST; PLUGIN (v4, A-aera-18: references/fast-standard-rules.md row 3.01-04) |
| font_color | `text` (000000) | SYNTHESIS D8; SURVEY C (72.0% black; not re-checked) |
| bold | yes | SYNTHESIS D8; SURVEY C (53.0% of 8,758 total-row formulas bold; not re-checked); GUIDES L-68 (not re-checked) |
| italic | no | PLUGIN |
| fill | none | SYNTHESIS D8; SURVEY C (95.6% unfilled; not re-checked) |
| border | top thin 000000 | GUIDES L-68 (top border, so rows can be inserted above; not re-checked); PLUGIN (black rule, design 10.2); SYNTHESIS D8 draws it in 808080, kept as the named override total_grey_rule |
| number_format | `item` | PLUGIN; SYNTHESIS D9 |
| align | `right` | SYNTHESIS D8; GUIDES L-67 (not re-checked) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### output

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | a key result (a block item or measure marked key: true) | PLUGIN |
| font_color | `text` (000000) | SYNTHESIS D8; R2 section 5 |
| bold | yes | SYNTHESIS D8 (key outputs bold, builder default); R2 section 5 (bold decision outputs) |
| italic | no | PLUGIN |
| fill | none | MAX (minimal colour); SYNTHESIS D8 |
| border | none | PLUGIN |
| number_format | `item` | PLUGIN |
| align | `right` | SYNTHESIS D8; GUIDES L-67 (not re-checked) |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

#### label

| Attribute | Value | Source |
| --- | --- | --- |
| meaning | free text outside a grid (captions on the cover, the 'Master check' label) | PLUGIN |
| font_color | `text` (000000) | PLUGIN |
| bold | no | PLUGIN |
| italic | no | PLUGIN |
| fill | none | MAX (minimal colour) |
| border | none | PLUGIN |
| number_format | `general` (`General`) | PLUGIN |
| align | `left` | PLUGIN |
| wrap | no | PLUGIN |
| locked | yes | GUIDES L-60 (not re-checked) |
| size | the sheet's size (8 or 9) | MAX |

### Rules

| Rule | Value | Source |
| --- | --- | --- |
| depth_limit | 2 | MAX (spec 1.2: 'less than 3 nested functions at most'); BOOKS B-17 (Rees PFM p113: two embedded logical steps, check B-17); FAST 3.03-01 gives no number (check C1.4) |
| length_limit | 100 | PLUGIN's own number (design D6): FAST 3.03-01 gives no character count (check C1.4); R2 section 1 sample P95 59 and P99 92 characters (check r2 #8); SMART: half the formula bar (check r2 #3) |
| allowed_literals | [0, 1] | PLUGIN (stricter than FAST 3.04-01 and PwC p.16, which allow 1, 0, -1 and occasionally 12 and 4, check L-14; IFM p163 accepts 0 and 1, check B-11) |
| check_tolerance | 0.001 | PLUGIN: R2 section 2 finds no universal tolerance; Fairhurst's 1-dollar tolerance is an example (check B-32) |
| helper_spare_rows | 0 | PLUGIN (design 7.2; v2 A-aera-6): a value above 0 is refused by build, because a helper row below the last raw row reads empty raw cells as 0 |
| formula_key_check_max_rows | 5000 | PLUGIN: the per-row key check SUMPRODUCT(--EXACT(K$2:Kr,Kr)) grows with the square of the row count |
| dense_fill_ratio | 0.6 | SYNTHESIS D18 (builder default): a sheet the plugin did not build is dense, so size 8, when at least this share of its used block holds a value (MAX: 'a brick wall of data then 8') |
| dense_min_cells | 500 | SYNTHESIS D18 (builder default): and its used block holds at least this many cells; otherwise size 9 (MAX: 'some white spaces then 9') |
| sparse_sheet_cells | 100 | PLUGIN's own number (r4 verdict A-aera-15, reduced scope): build notes a calculation sheet that takes size 8 from its role and holds fewer populated cells than this (I-SPARSE-SHEET, information); choosing the size from measured density stays a decision for Max (SYNTHESIS D18). Measured on the v4 shipped models: calculation sheets hold 40 (starter, font_size 9 set), 98, 165, 223, 259 and 289 populated cells |

### Named overrides

| Name | What it changes | Source |
| --- | --- | --- |
| input_blue_font | inputs in blue font without a fill (the research synthesis's recommended default, SYNTHESIS D16 option A): input.font_color = `input_blue` (0000FF); input.fill = none | SYNTHESIS D16 option A (fewest colours, MAX); SURVEY F (Kaplan 0000FF on 86.4%, check #7; CFI 3271D2 or 0000FF on 87.2%, check #8); GUIDES L-50 (CFI p.83, check L-50) |
| input_blue_on_fill | inputs in blue font on the light yellow fill (the look of Max's own files): input.font_color = `input_blue` (0000FF) | SYNTHESIS D16 option B; GUIDES C-1 and C-4 (Max's own files: blue numbers on amber or light yellow cells); BOOKS section B (IFM p209 blue on yellow, FMD p51 blue on beige, check SecB rows); PLATFORMS C3.2 (Wall Street Prep: blue text on a yellow background, check C3.2) |
| links_green | references to other sheets in green font: import.font_color = `link_green` (008000) | SYNTHESIS D17 option; SURVEY pass 2 (Max's prior work, check #12); GUIDES L-50 (CFI p.83, check L-50); GUIDES C-1 (Max's legend: green font = cross-sheet link) |
| fast_import_export | FAST 1.01-06 in part: importing cells in blue font; exports in red font only in a workbook with no manifest, where theme gives a calculation that another sheet reads the export type (section 6); build gives no cell the export type, so in a built model the override colours the importing cells only and the cells they read stay black: import.font_color = `input_blue` (0000FF); export.font_color = `export_red` (FF0000) | FAST 1.01-06 (check C1.9); SYNTHESIS D17 does not use it by default, because blue marks inputs in input_blue_font and red marks failing checks |
| unit_unfilled | the unit column without the row-header grey (grey on row labels only): unit.fill = none | SYNTHESIS D18 (Max named grey for column and row headers only; unit and key columns unfilled adds the least grey, MAX minimal colour) |
| total_grey_rule | the top rule of total rows in grey 808080 instead of black: total.border = top thin 808080 | SYNTHESIS D8 (total: thin top 808080); GUIDES L-69 (grey rather than black borders; not re-checked) |

<!-- end of generated tables -->

# FAST Standard rules used by this skill

This file lists the rules of the FAST Standard that the skill's own rules rest on, with their rule numbers, and says for each one what the skill does with it. Read it when an audit finding cites a FAST rule, when you must explain why the skill refuses or flags something, or when a user asks which published standard a rule comes from.

## Attribution and licence

- Source: FAST Standard 02c, July 2019, by the FAST Standard Organisation, https://www.fast-standard.org/ (the PDF: https://fast-standard.org/wp-content/uploads/2019/10/FAST-Standard-02c-July-2019.pdf).
- Licence: the FAST Standard is licensed under the Creative Commons Attribution 4.0 International License, https://creativecommons.org/licenses/by/4.0/ (platforms C1.2, not confirmed: read the licence page https://fast-standard.org/fast-standard-creative-commons-licence/ before publishing this file).
- Changes: text in quotation marks is quoted from FAST 02c. Everything else is a summary in this file's own words. The column "What the skill does" is this plugin's, not the FAST Standard Organisation's.
- Edition: no FAST edition newer than 02c was linked from fast-standard.org on 2026-11-24 (platforms C1.1 and E13, not confirmed).
- Rule numbers follow FAST 02c Appendix C, "The Rules in Short" (platforms C1.3, not confirmed). The margin numbers in the body text do not line up with the rules when the PDF is read as text (platforms C1.3, not confirmed; the claim check of C1.4 noted the same).

## Evidence labels

- "confirmed": a claim check in the research (`research/verify/`) read FAST 02c and found the rule. The id names the item: "platforms C1.39" is item C1.39 in `platforms_standards_check_v1.json`; "researcher 2 #1" is item 1 in `researcher2_check_v1.json`.
- "not confirmed": the platforms and standards research report states the rule, but no claim check read it. Treat the rule number and the wording as the report's reading of FAST 02c.
- The research files are in the project folder (`01.PROJECT/20261124_create_skill_ruleofthumb_financial_modeling/20.PROCESS/research/`), not in the plugin: the report is `20261124_create_skill_ruleofthumb_financial_modeling_research_platforms_standards_v1.md`, the checks are in `verify/`.
- "design n": section n of the plugin's design (`rotm_design_v1.md`, kept in the project's build folder, not shipped). It describes what the plugin does.
- "Max, spec 1.x": Max's own words, quoted from Part 1 of the job's specification. They are fixed points, not claims to check.

## The thumb rule and how this skill measures it

- FAST 3.03-01: "Do not write a formula longer than your thumb" (FAST 02c p. 36; platforms C1.4, confirmed; researcher 2 #1, confirmed).
- FAST's explanation: "a formula longer than your thumb likely means that it should be broken into more than one step." (platforms C1.4, confirmed).
- FAST gives no character count and no nesting count for the thumb (platforms C1.4, confirmed; researcher 2 #1, confirmed).
- Max's own measure: "the realistic approach is having less than 3 nested functions at most. (if we require something more than that perhaps we need a helper column)" (Max, spec 1.2). The skill turns this into a depth limit of 2 nested functions, one setting (`rules.depth_limit` in `style_dictionary.json`), checked by audit rule F-DEPTH (error).
- The skill also measures length: a formula longer than 100 characters, including the `=`, is finding F-LENGTH (warning). 100 is the plugin's own number, because FAST gives none (design D6). It is one setting (`rules.length_limit`).
- How depth is counted: [helper-patterns.md](helper-patterns.md#the-counting-rule), and `rotm.py formula "=..."` prints the depth of one formula.

## Rules the skill enforces or follows

Each row: the rule number, the rule (a quote where the claim check confirmed the wording, otherwise a summary), the evidence label, and what the skill does with it. Audit rule ids (F-DEPTH and so on) are defined in `scripts/lib/audit.py`, and every audit report names the rule of each finding. The procedure that uses them is in [SKILL.md](../SKILL.md).

### Formulas (FAST section 3.03)

| Rule | What it says | Evidence | What the skill does |
| --- | --- | --- | --- |
| 3.03-01 | "Do not write a formula longer than your thumb" | platforms C1.4, confirmed | F-DEPTH (error) at depth over 2; F-LENGTH (warning) over 100 characters |
| 3.03-02 | "No formula should take more than 24 seconds to explain" | researcher 2 #1, confirmed | no audit rule: a machine cannot time an explanation; the depth and length limits stand in for it |
| 3.03-03 | write each formula on one line, with no line breaks inside it | researcher 2 #1, confirmed | F-LINEBREAK (warning) |
| 3.03-04 | use a limited set of Excel functions | researcher 2 #1, confirmed | the function catalogue ([functions.md](functions.md)): allowed, flagged (F-FUNC-FLAGGED, warning) and refused (F-FUNC-REFUSED, error) functions |
| 3.03-05 | "Use flags to limit use of IF function" (Appendix C) | platforms C1.39, confirmed | helper patterns turn conditions into 0/1 flag cells ([helper-patterns.md](helper-patterns.md)) |
| 3.03-06 | "Use INDEX (or even CHOOSE) over IF to pick values" (Appendix C) | platforms C1.39, confirmed | helper patterns replace IF ladders with a lookup table read by MATCH and INDEX; CHOOSE is flagged (see 4.01-01) |
| 3.03-07 | "Never use nested IFs" (p. 37) | platforms C1.39, confirmed; researcher 2 #1, confirmed | F-NESTED-IF (error), even when the formula is inside the depth limit |
| 3.03-08 | do not use Excel Names; exceptions allowed for external links, macro references, ingredients that cannot be shown locally (for example a check tolerance), data validation lists and text criteria in conditional formats (3.03-08.1 to .6) | platforms C1.40 and A2.6, not confirmed | F-NAME (warning); `build` writes no defined names at all, so the check tolerance is an input cell, not a name (design 6.6) |
| 3.03-09 | do not construct array formulas, except data tables, cases that cannot be done without arrays, or cases where avoiding arrays is harder to review | platforms C1.41, not confirmed | F-ARRAY (warning); spilling functions (FILTER, UNIQUE, SORT, SEQUENCE and others) are flagged in the catalogue |
| 3.03-11 | beware circularity or error values hidden on the branch of an IF that is not taken | platforms C1.42, not confirmed | V-ERROR sees only the values Excel shows. The lookup patterns put INDEX on a branch that is not taken when the key is missing, and count the missing keys in a check instead (helper-patterns.md H6) |

### Constants, references and consistency (FAST sections 3.01, 3.02, 3.04)

| Rule | What it says | Evidence | What the skill does |
| --- | --- | --- | --- |
| 3.01-01, 3.01-03 | keep constants visibly apart from time series; do not lay out constants as series | platforms C1.28, not confirmed | every typed number sits on the `inputs` sheet or on a raw sheet (design 6.4) |
| 3.01-04 | do not use row totals in model logic; display totals are for display only | platforms C1.29, not confirmed | block totals are display totals (design 6.5): a grid block's total row sums the rows above it in each period column. Checks read block totals, and so do calculations: an items block links to another block's total row with `{b:<block>.total}`. In the worked example `examples/sales_vs_target`, the bridge rows `calc!D37` `=D$10` (the total of the actual block) and `calc!D38` `=D$18` (the total of the target block) link to the block totals of the region blocks, and the variance rows below them read those links |
| 3.01-08 | format numbers to show what they are (for example four decimals for factors) | platforms C1.30, not confirmed | number formats come from the dictionary (`factor` is `0.0000`) |
| 3.02-01 | formulas are consistent along the axis | platforms C1.31, not confirmed | H-INCONSISTENT (error with a manifest) for helper columns; B-INCONSISTENT (warning) for block rows |
| 3.02-02 | temporary code is marked with a square-bracket label and yellow shading | platforms C1.53, confirmed (yellow shading); platforms C1.31 (label), not confirmed | the skill has no temporary code: a formula that differs from its row is a finding, and deliberate exceptions are listed in the specification |
| 3.02-03 | do not use partial range references | platforms C1.32, not confirmed | `build` reads raw and helper data as whole columns (`raw_sales!$F:$F`), so no range stops short of the data (design 7.1) |
| 3.04-01 | do not write formulas with embedded constants, except universal constants such as 24, 12 or 1000 | platforms C1.43, not confirmed | F-LITERAL (warning; a refusal at build time). Only 0 and 1 are allowed by default, which is stricter than FAST; a model adds others with `settings.allowed_literals` (design 8.3) |
| 3.04-02 to 3.04-07 | put spaces between arguments; avoid unnecessary parentheses; use `-1 *` for sign switches; leave out the current sheet's name; do not use headings or blank cells in logic; do not over-anchor | platforms C1.44, not confirmed; which number belongs to which item is not confirmed | F-SELF-SHEET (warning) for a reference that names its own sheet; `build` anchors references as design 6.6 says. The skill's examples put no spaces between arguments, which departs from this group |

### Layout, flow and flags (FAST sections 1, 2 and 3.05 to 3.07)

| Rule | What it says | Evidence | What the skill does |
| --- | --- | --- | --- |
| 1.01-01 | group sheets into four classes: foundation (inputs, flags), workings, presentation and control | platforms C1.5, not confirmed | sheet types: `inputs`; `raw`, `helpers`, `calc`; `output`; `cover`, `checks`, `notes` (design 6.2) |
| 1.01-02 | use the same column structure on every sheet | platforms C1.6, not confirmed | every block sheet: A spacer, B labels, C units, D first value column (design 6.3) |
| 1.01-03, 1.01-04 | one time ruler throughout the model | platforms C1.7, not confirmed | a timeline's headers are `=EOMONTH(<start>,0)` and then `=EOMONTH(<left>,1)` (design 6.5) |
| 1.01-06 | "Mark exports with red font and imports with blue font" (p. 17) | platforms C1.9, confirmed; researcher 2 #9, confirmed | not the default: links are black. The named override `fast_import_export` in `style_dictionary.json` applies FAST's colours. In a model the plugin builds it colours the importing cells only, because build gives no cell the export type; exports turn red only when `theme` styles a workbook that has no manifest |
| 1.01-07 | calculate once and link to the first calculation everywhere else | platforms C1.10, not confirmed | one helper column per step; later blocks read the helper column |
| 1.01-11 | never release a model that uses circularity on purpose | platforms C1.11, not confirmed | W-ITERATE (error) when iterative calculation is on |
| 1.02-01 | calculation flows left to right across sheets; input and result sheets may sit at the front | platforms C1.12, not confirmed | `build` writes sheets in the order cover, inputs, raw, helpers, calc, output, checks, notes (design 6.2) |
| 1.02-02 | do not optimise calculation and presentation on the same sheet | platforms C1.13, not confirmed | `calc` and `output` are separate sheet types |
| 1.02-03 | put flags and factors on dedicated sheets | platforms C1.14, not confirmed | departure: row-level flags are helper columns on `h_<table>`, period flags are line items of a calc block; there is no separate time sheet |
| 2.01-01 | "Each column should have a single and consistent purpose" | platforms C1.16, not confirmed | the fixed column structure of design 6.3 |
| 2.01-04, 2.01-05 | logic flows top to bottom and left to right; counter-flows are marked with grey shading | platforms C1.18, not confirmed (flow); platforms C1.53, confirmed (grey shading for counter-flows, 2.01-05) | One intended exception to the calculation direction: the master check `checks!C2` sits at the top of the checks sheet and reads the check results below it and to its right; in the worked example it is `=--(OR(SUM(I5:I30)>0,COUNT(I5:I30)<>COUNTA(B5:B30)))`. It sits there because FAST 2.03-05 asks for the master check in the freeze pane, and C2 of every other block sheet links to it (`=checks!$C$2`). The plugin does not shade this cell grey. In the built worked example `examples/sales_vs_target/model.xlsx`, `checks!C2` is the only formula that reads a cell of its own sheet below it or to its right (scanned 2026-11-25, and again on the v7 build of 2026-11-26). The plugin's greys are fills of cell types, not counter-flow marks: column headers D9D9D9, row headers F2F2F2, and the unit column F2F2F2, as `style-dictionary.md` says (the unit cell `calc!C6` of the worked example has fill F2F2F2). Max's words on grey: "light shade gray for the column headers and slightly less shade gray for row headers" (Max, spec 1.2) |
| 2.01-08 | do not hide anything, except undefined time | platforms C1.19, not confirmed | S-HIDDEN (warning) |
| 2.02-01 | build every calculation in its own block, with its ingredients as links immediately above it; exceptions: corkscrews, cascading calculations, trivial formulas and two-dimensional line items | platforms C1.20, not confirmed | calculation blocks of three shapes (grid, items, measures); helper rows go above the row that uses them (design 6.5) |
| 2.02-05 | use corkscrew blocks for balances, not cumulative SUMs | platforms C1.22, not confirmed | an items block with `{prev}` (previous column) builds a corkscrew (design 6.5) |
| 2.02-06 | use timing flags routinely; conditional timing logic inside complex formulas should never exist | platforms C1.23, not confirmed | helper patterns H1 and H2 |
| 2.03-05 | show master error checks and alert indicators in the freeze pane | platforms C1.24, not confirmed | the master check sits in C2 of every block sheet, inside the frozen rows on the inputs, calc, output and checks sheets (design 6.2, 6.3). Departures: the cover and notes sheets have no frozen rows; raw and helper sheets show no master check, because row r of a helper sheet must line up with row r of its raw sheet |
| 2.04-02 | input sheets carry a visible instruction or comments column; do not use cell comments for this | platforms C1.25, not confirmed | column E of the inputs sheet holds each input's source or note (design 6.4) |
| 2.05-05 | provide keys to colour coding, abbreviations, names and functions | platforms C1.26, not confirmed | the cover sheet holds the cell-type legend (design 6.2) |
| 2.06-01, 2.06-02 | control sheets hold a table of contents and a list of the model's limitations | platforms C1.27, not confirmed | the cover sheet holds contents and the specification's `limitations` (design 6.2) |
| 3.05-01 to 3.05-13 | every line item has a unique label and a units designator | platforms C1.45, not confirmed | column C holds the unit of every row; contract columns carry a unit |
| 3.06-01, 3.06-02 | row-anchor links; "Do not create daisy chains; do not link to links" | platforms C1.46, not confirmed | no audit rule |
| 3.07-01 to 3.07-04 | use timing flags, create a flag only when needed, give flags display totals; a flag holds only 0 or 1 | platforms C1.47, not confirmed | check results and helper flags hold 0 or 1 ([checks.md](checks.md)) |

### Functions and formats (FAST section 4)

| Rule | What it says | Evidence | What the skill does |
| --- | --- | --- | --- |
| 4.01-01 | prefer INDEX over CHOOSE | platforms C1.39, confirmed (section 4.01 prefers INDEX over CHOOSE); the number 4.01-01: platforms C1.48, not confirmed | CHOOSE is flagged (F-FUNC-FLAGGED) |
| 4.01-03 | "Do not use OFFSET or INDIRECT functions" | platforms C1.48, not confirmed | OFFSET and INDIRECT are refused (F-FUNC-REFUSED, error) |
| 4.01-04 | do not apply ROUND everywhere; round only where it is needed | platforms C1.49, not confirmed | checks compare with ABS against a tolerance, never with ROUND ([checks.md](checks.md)) |
| 4.02-01 | "Use well-defined format styles"; do not stay with Excel's defaults | platforms C1.50, not confirmed | every cell gets its look from `style_dictionary.json` by cell type; `build` registers one named style per cell type (design 10.5) |
| 4.02-02 | "Do not merge cells"; use centre across selection | platforms C1.51, not confirmed | S-MERGED (warning) outside raw sheets; R-MERGED (warning) on raw sheets |
| 4.03-01, 4.03-02 | where names are used, anchor them fully and scope them to the workbook | platforms C1.52, not confirmed | not needed: `build` writes no names |

### Definitions (FAST Appendix B)

- A check is an integrity cross-check that should never fail in any scenario (platforms C1.54, not confirmed). In the skill: a check of severity `error` (the default), part of the master check.
- An alert flags a business issue, such as a covenant breach (platforms C1.54, not confirmed). In the skill: a check of severity `alert`, shown in its own table and not part of the master check ([checks.md](checks.md)).
- An input is any cell without cell references; a link is a single-cell reference with no functions or operators (platforms C1.54, not confirmed). In the skill: cell types `input`, `link` and `import` in `style_dictionary.json`.

### Colour in FAST 02c

- FAST 02c names colours only for exports (red font) and imports (blue font) (1.01-06), counter-flows (grey shading, 2.01-05) and temporary code (yellow shading, 3.02-02) (platforms C1.53, confirmed).
- Appendix A, "FAST Formatting", is a placeholder: "Consensus position being developed for subsequent draft." (platforms C1.53, confirmed).
- FAST 02c gives no colour for inputs or for calculations (platforms C1.53, confirmed; the check found it by a text search).

## Where the skill departs from FAST, and why

1. The thumb gets numbers. FAST gives none (platforms C1.4, confirmed). Depth 2 is Max's limit (spec 1.2); 100 characters is the plugin's own choice (design D6).
2. Every function call counts toward depth, including type conversions such as VALUE and N. Rees treats a VALUE that does no "genuine manipulation" as no extra logical step (books B-17, confirmed: PFM p. 113). The skill counts it, so the rule stays mechanical (design 8.1).
3. Nested IF is an error even inside the depth limit: `=IF(a,IF(b,x,y),z)` has depth 2 and still fails F-NESTED-IF, because FAST says "Never use nested IFs" (platforms C1.39, confirmed).
4. Only 0 and 1 may be typed into a formula by default. FAST allows universal constants such as 24, 12 or 1000 (platforms C1.43, not confirmed). A model allows more numbers with `settings.allowed_literals`.
5. No defined names at all, including the ones FAST allows (platforms C1.40, not confirmed). Reason: the workbook must work in Excel and in Google Sheets, and every reference stays a visible A1 reference (design 6.6).
6. Inputs are marked by a fill, not by FAST's colours. FAST names no input colour (platforms C1.53, confirmed). The default input look is black text on fill FFF2CC; it is not FAST's temporary-code yellow, because the skill has no temporary code. The choice between the fill and a blue font is listed for Max (design 10.3).
7. Imports and exports are black by default; FAST's blue and red are the named override `fast_import_export` (design 10.3). In a model the plugin builds, the override colours the importing cells only (blue): build gives no cell the export type, so the cells the imports read stay black. Measured on 2026-11-25: a starter model built with `"use": ["fast_import_export"]` has 7 import cells in blue 0000FF and 0 export cells. Exports turn red only when `theme` styles a workbook that has no manifest.
8. Raw and helper sheets show no master check (design 6.3). On the inputs, calc, output and checks sheets the master check is in C2, inside the frozen rows, so it stays in view. On the cover and notes sheets it is in C2 at the top of the sheet, with no frozen rows, so it scrolls out of view (row 2.03-05 above).
9. Flags live on helper sheets and in calc blocks, not on a dedicated flag sheet (rule 1.02-03).
10. The skill's examples put no spaces between function arguments (rule group 3.04-02 to 3.04-07, not confirmed). Spaces count toward the 100-character length (design 8.2).

## Other standards and books, in this file's own words

- ICAEW Financial Modelling Code (2024): ICAEW reserves all rights, so its text is summarised here and not quoted (platforms C2.1, not confirmed).
  - It asks that input cells be told apart by a fill colour or a border, or both, and not by font colour alone; it names no colour (platforms C2.2, confirmed; Code printed p. 5).
  - It mentions "no formulas longer than your thumb", "no line breaks" and "no more than five operands in any cell" as rules some modellers use, not as its own rules, and says some calculations are clearer when such rules are broken (platforms C2.3, confirmed; researcher 2 #4, confirmed; Code printed p. 16).
- ICAEW 20 Principles (2024): headlines include a clear flow of inputs, processes and outputs; consistent formulas across ranges; the simplest features for the task; no fixed values inside formulas; and checks, controls and alerts built in from the start (platforms C2.5, not confirmed).
- SMART (Corality, now Forvis Mazars): guideline 04 limits a formula to about half the width of the visible formula bar and gives no character or depth number (researcher 2 #3, confirmed). No other SMART rule was checked.
- BPM / SSRB Best Practice Spreadsheet Modelling Standards: not confirmed. The publisher's site refused the connection on 2026-11-24, and only an unofficial 2010 copy was found (researcher 2 #5, not checkable; platforms C2.8 and E10). The skill uses no BPM rule.
- Bastick: "Excel formulae in your formula bar should be no longer than your thumb", with no number (books B-16, confirmed: IFM p. 13).
- Rees: "One should generally avoid using more than two embedded logical steps." (books B-17, confirmed: PFM p. 113).
- Fairhurst: do not do more than two or three things in one cell; break the work into rows or calculation blocks (books B-18, confirmed: FMD p. 309).

## Not confirmed

These four topics have no primary source that a claim check confirmed. Do not state them as facts to a user.

1. Whether the cells of a Connected Sheets extract can be typed over, and what happens when they are (platforms E1). See [google-sheets.md](google-sheets.md).
2. What a refresh that changes an extract's row count does to formulas that point into or below the extract (platforms E2). See [google-sheets.md](google-sheets.md).
3. What survives when an `.xlsx` written by openpyxl is converted to Google Sheets: fonts, fills, number formats, named styles, conditional formats, validation, names, freeze panes, gridlines, tables, notes (platforms E5). See [google-sheets.md](google-sheets.md).
4. The SMART and BPM primary texts: SMART guideline 04 is confirmed (researcher 2 #3, confirmed); every other SMART rule and every BPM rule is not (platforms E9 and E10; researcher 2 #5, not checkable).

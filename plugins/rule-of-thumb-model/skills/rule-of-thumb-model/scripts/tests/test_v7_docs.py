"""Tests of the v7 documents (package P8 of the v7 round, 2026-11-26). Owner: P8.

Each test reads the place where the v7 plan (section 5.4) puts a text and checks that the text is there, in words
that do not depend on line breaks (whitespace is made uniform and code-span backticks are removed):

- T-1, T-2 ([C-sol-8]): SKILL.md P1 step 1 (the sample header of each column) and P2 (the header line land --csv
  accepts); P2's rule for a land refusal stays.
- T-3 ([J-2, J-3, J-4] part (3)): SKILL.md step 0 and P2: raw/ holds only the snapshots land writes; land's note on
  the other files; land --csv of the snapshot path itself.
- T-4 ([A-aera-2, C-sol-5]): SKILL.md step 8: --refresh --strict, the X-NO-QUERY sentence, the read-back at
  check --accept-refresh.
- T-5 ([A-aera-4, A-aera-13]): SKILL.md P5: the per-cell check of a ratio grid and W-CHECK-GUARD.
- T-6 ([A-aera-8]): SKILL.md Inputs, P7 step 2 and "Not verified" (both files): functions only Google Sheets has.
- T-7 ([J-1]): SKILL.md P3: the pointer to helper-patterns.md, "Rank or percentile within a group".
- T-8 ([J-2, J-3, J-4] part (2)): SKILL.md P7 step 3 names the list files of render.json and its fields; the line
  render prints for a cut sheet. The quoted line is the one excel.py prints; render.json holds the named fields
  (without Excel); with Excel, a sheet of 300 rows gets its last row and column in its line and in render.json.
- T-9 ([A-aera-11, A-aera-15]): SKILL.md P6: conditional formats, T-CF, S-CF.
- T-10 ([A-aera-3, C-sol-2]): SKILL.md P2: the kept copies of a workbook with a live query.
- T-11 ([J-2, J-3, J-4] part (1)): SKILL.md P7 step 3: I-TEXT-WIDTH.
- T-12: skills/build/SKILL.md: W-CHECK-GUARD, I-TEXT-WIDTH, I-SHEETS-ONLY and the kept copies.
- [J-6]: SKILL.md and README.md "What is and is not verified": the version line, the subsection "Runs on the final
  v7 files" (the placeholder, or the integrator's dated runs that name the required runs), no v6 run statement, the
  four v7 items with their tests and the integrator's sentence (before the runs every v7 item has it, after them
  none), Google Sheets only under "Not verified". test_v2_docs.py compares the runs of the two files. v8 (r7 verdict
  [J-2]): the subsection became "Runs on the final v8 files", and v9 (r8 verdict [J-2]) "Runs on the final v9
  files"; test_v9_docs.py tests it and the v9 items; the tests here keep only what still holds of v7 (the v7 subsection is gone, no v6 run text, the four v7 items with their
  tests and without the integrator's sentence).

The rule ids these documents name come from the code of other packages (P3 build.py, P4 check.py, P5 audit.py);
test_references.py::test_rule_ids_mentioned_exist checks the references against the code (plan section 7, I-1).

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v7_docs.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

ROTM = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py")]

# ----------------------------------------------------------------------------- the texts of plan section 5.4

T1 = ("With --csv, propose records for each column the sample header it was read from (csv_header in the contract "
      "JSON). When a header differs from its destination name (for example Order Date proposed as order_date), the "
      'contract .md shows the column "CSV header" beside "Column", and "Points to confirm" lists each header with its '
      "destination name. After agreement, land --csv accepts that file as it is (P2).")
T2 = ("The header line land --csv accepts: the destination names in the contract's order; or, when the contract "
      "records a sample header for every column (csv_header, P1 step 1), each of those headers exactly once, in any "
      "order, compared after trimming spaces, and no other column. In the second form land reads the columns by their "
      "headers, writes raw/<t>.csv with the destination names in the contract's order, and prints each header with "
      "its destination name. Any other header is L-COLUMNS: nothing is written and the fix names both forms; report "
      "it and stop, as below. A contract proposed with --columns, or before v7, records no sample headers: its CSV "
      "needs the destination names.")
LAND_REFUSAL_RULE = ["When land refuses an extract", "Then stop: land nothing for that table and build nothing on it.",
                     "Do not get past the refusal yourself."]
T3_STEP0 = ("MODEL/raw/ holds only the snapshots land writes (raw/<t>.csv and raw/<t>.controls.json). A file of the "
            "user's found there is copied to MODEL/data/ and landed from there; the report names the files left in "
            "raw/.")
T3_P2 = ("land prints a note that lists the files in raw/ that it did not write. land --csv MODEL/raw/<t>.csv (the "
         "snapshot path itself) reads the whole file before it writes anything: a refused landing leaves the file "
         "byte for byte, and an accepted one writes every row back, with the destination names in the header.")
T4_STEP_1 = "1. ROTM check MODEL --refresh --strict refreshes every query through Excel"
T4_STEP_5 = "5. After a refresh by hand in Excel (Data > Refresh All), run ROTM check MODEL --refresh --strict"
T4_SUB = ("Give --strict: without it a warning such as X-NO-QUERY leaves exit 0, so read the report's refreshed value "
          "(data.refreshed in MODEL/reports/check.json). X-NO-QUERY stays a warning (an error with --accept-refresh).")
T4_READ_BACK = ("With Excel, check --accept-refresh reads the query back and compares it too, also when it is given "
                "without --refresh: on a difference it reports X-QUERY-CHANGED as an error, accepts nothing, and "
                "raw/<t>.csv, raw/<t>.controls.json and the manifest stay byte for byte. Without Excel, or for a "
                "manifest without the hash, the acceptance prints the note that the query was not compared. A value "
                "typed into a raw sheet that a query loads is not found by an acceptance alone: run check --refresh "
                "first, which loads the rows from the source again.")
T4_OLD = ["check --accept-refresh alone does not read the query again",
          "The query is read back only by check --refresh"]
T5_COUNT = ("Counting a block's numeric cells (COUNT) is not an independent total: it holds whatever values the cells "
            "show.")
T5_RATIO = ("A block of ratios gets a check of each cell. The summed product (SUMPRODUCT(<ratio grid>,<denominator "
            "grid>) against the sum of the numerator where the denominator is not 0) does not find a grid in which "
            "every cell holds the overall rate, because that grid gives the same sum. Add a block whose cell is the "
            "absolute difference between the ratio times its own denominator and that cell's numerator, "
            "ABS({g:<ratio>}*{g:<denominator>}-({g:<denominator>}<>0)*{g:<numerator>}), and an at_most check that its "
            "largest value (MAX({blk:<that block>})) is at most {in:check_tolerance}. A cell whose denominator is 0 "
            "shows 0 % and gives 0 in that block, so it is left out, as in the summed product. The worked example has "
            "both checks (references/checks.md).")
T5_GUARD = ("A check expression (actual or expected) that holds IFERROR or IFNA makes build warn W-CHECK-GUARD (strict "
            "exit 1), and audit reports W-CHECK-GUARD at the check cell: an error in an operand must show in the "
            "result (references/checks.md, rule 4).")
T5_OLD = "A block of ratios gets a check that ties it back to the amounts it divides"
T6_INPUTS = ("With sheets, a function that only Google Sheets has (for example JOIN, SPLIT or REGEXMATCH; "
             "references/functions.md lists them) cannot be calculated by Excel, so such a model cannot be verified "
             "in Excel. Prefer the alternative the catalogue names (for JOIN the & operator). Build prints "
             "I-SHEETS-ONLY (information) for each such function.")
T6_P7 = ("A workbook that holds a function only Google Sheets has: check reports X-SHEETS-ONLY (information) with the "
         "functions and their cells, does not open the workbook in Excel, leaves the file byte for byte and exits 3 "
         '(not recalculated). Say in the report "audited, not recalculated: functions only Google Sheets has".')
T6_NOT_VERIFIED = ("A model that uses a function only Google Sheets has: it is audited and not recalculated "
                   "(X-SHEETS-ONLY); no Google Sheets session calculated it.")
T7 = ("A helper column may not read other rows of its own helper sheet: build refuses {h:<table>.<column>} there and "
      'its fix names references/helper-patterns.md, "Rank or percentile within a group", which gives the pattern for '
      "a rank or a percentile within a group.")
RENDER_LINE = ("the sheet's used range ends at row <last_row>, column <last_col>: rows or columns beyond the rendered "
               "range are not shown")
T8 = ("MODEL/reports/render/render.json holds the list files, one entry per rendered sheet, with the fields sheet, "
      "file, truncated (true when the sheet's used range is larger than the render), last_row and last_col (the last "
      f'row and column of the used range). The line render prints for such a sheet ends with "{RENDER_LINE}".')
T9 = ("The audit compares the fill stored on each cell; it does not read the colours of conditional-format rules "
      "(theme reports such rules as T-CF). With a manifest, any conditional-format rule is S-CF (a warning, one per "
      "sheet), because build writes none. A colour drawn by such a rule is seen only in the renders.")
T10 = ("A rebuild of a workbook whose raw sheet holds a live query that rotm.py attached: before build replaces the "
       "workbook and its manifest, it copies both to <stem>.bak_<YYYYMMDD_HHMMSS>.xlsx and "
       "<stem>.manifest.bak_<YYYYMMDD_HHMMSS>.json. When every query is attached again, both copies are removed. When "
       "an attach fails (for example the source cannot be reached) or Excel is not available, the copies stay and "
       "W-PQ-NOT-ATTACHED names them: to go back, copy both over the new files; to go on, make the source reachable "
       "and run ROTM land MODEL --table <t> --power-query. The hooks do not audit the copies.")
# Added by P8 so that the step agrees with the hooks table (the raw-sheet guard refuses a copy over a workbook).
T10_GUARD = ("The raw-sheet guard refuses a shell command that copies onto an existing workbook, so the user copies "
             "the two files back by hand.")
T11 = ("Build prints I-TEXT-WIDTH (information; the strict exit code does not change) for a label, unit or note text "
       "that is wider than the room its cell has (the column's width and, for text that may run on, the free cells to "
       "its right). The note names the JSON path, the length of the text and the room. Shorten the text or move the "
       "detail into a note and rebuild; then look at the render.")
T12_GUARD = ("W-CHECK-GUARD (a warning, strict exit 1): a check expression of the specification (actual or expected) "
             "holds IFERROR or IFNA; an error in an operand must show in the check's result "
             "(<SKILL_DIR>/references/checks.md, rule 4). Remove IFERROR or IFNA from the check; for a division that "
             "can be by zero, take the zero case on the other side, for example --(<denominator><>0)*<numerator>.")
T12_WIDTH = ("I-TEXT-WIDTH (information; the strict exit code does not change): a label, unit or note text that is "
             "wider than the room its cell has (the column's width and, for text that may run on, the free cells to "
             "its right). The note names the JSON path, the length of the text and the room. Shorten the text or "
             "move the detail into a note and rebuild; then look at the render.")
T12_SHEETS = ("I-SHEETS-ONLY (information; product sheets): a function only Google Sheets has (for example JOIN), with "
              "its count of formula cells and the first cell. Excel cannot calculate it, so rotm.py check does not "
              'recalculate the workbook (X-SHEETS-ONLY, "audited, not recalculated: functions only Google Sheets '
              'has"). Use the alternative the note names (for JOIN the & operator) if the model is to be checked in '
              "Excel.")
NEW_RULE_IDS = ("W-CHECK-GUARD", "I-TEXT-WIDTH", "I-SHEETS-ONLY", "X-SHEETS-ONLY", "S-CF")

# ----------------------------------------------------------------------------- [J-6] (plan sections 5.3 and 5.5)

RUNS_V7 = "### Runs on the final v7 files"
P_RUNS_V7 = "Integrator: replace this line with the runs on the final v7 files."
KEEP = "Integrator: keep this item only when these tests passed in the final runs."
VERSION_V7 = "Version v7 of 2026-11-26"
RUNS_V8 = "### Runs on the final v8 files"     # v8 (r7 verdict [J-2]): the subsection that replaced RUNS_V7
RUNS_V9 = "### Runs on the final v9 files"     # v9 (r8 verdict [J-2]): the subsection that replaces RUNS_V8
V7_VERIFIED = {
    "Landing a CSV by its source headers (v7)": [
        "test_v7_contract.py::test_csv_with_spaced_headers_lands_by_its_sample_headers",
        "test_v7_contract.py::test_sample_header_landing_refuses_a_missing_extra_or_repeated_header",
        "test_v7_contract.py::test_plain_headers_land_as_before",
        "test_v7_contract.py::test_contract_without_csv_header_refuses_a_differing_header",
        "test_v7_contract.py::test_shipped_contracts_keep_their_hash_md_and_snapshot"],
    "The ar_aging over-90 measure (v7)": [
        "test_v7_templates.py::test_ar_aging_over90_follows_the_cutoff_when_the_last_bucket_changes",
        "test_v7_templates.py::test_ar_aging_shipped_lists_give_the_shipped_over90",
        "test_v7_templates.py::test_ar_aging_cutoff_input_is_fixed_at_90"],
    "The hooks on a bare folder name (v7)": [
        "test_v7_hooks.py::test_agreement_guard_refuses_an_untyped_reply",
        "test_v7_hooks.py::test_audit_on_write_reports_after_a_build",
        "test_v7_hooks.py::test_finish_check_refuses_once_for_an_unverified_workbook",
        "test_v7_hooks.py::test_a_bare_word_that_is_no_model_folder_gives_no_output"],
    "The kept copy of a workbook with a live query (v7)": [
        "test_v7_build.py::test_failed_attach_keeps_the_earlier_workbook_and_manifest",
        "test_v7_build.py::test_successful_rebuild_leaves_no_kept_copy",
        "test_v7_build.py::test_rebuild_without_excel_keeps_the_copies",
        "test_v7_build.py::test_hooks_do_not_act_on_the_kept_copies"],
}
# Numbers and words of the v6 runs that v7 must not carry ([J-6]: do not carry over the v6 numbers).
V6_RUN_TEXTS = ("2,821", "168 skipped", "2,437", "497 skipped", "158 passed", "05:13 to 05:21", "05:05 to 05:12",
                "04:03 to 05:01", "05:13 to 05:16", "05:13 to 05:19", "04:00 to 04:02", "04:02:23", "03:53:01",
                "324 files", "checks 24, 13 and 14", "Version v6", "on the final v6 files", "for v6 only",
                "Runs on the final v6 files")
# v8 and v9: the runs the integrator states are required in test_v9_docs.py (REQUIRED_RUNS there).


# ----------------------------------------------------------------------------- helpers

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _plain(text: str) -> str:
    """Flattened, without code-span backticks (the texts of plan 5.4 are given without code spans)."""
    return _flat(text.replace("`", ""))


def _section(text: str, heading: str) -> str:
    """The text from `heading` to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading)
    rest = text[start + len(heading):]
    m = re.search(r"^#{1,%d} " % level, rest, flags=re.M)
    return heading + (rest[:m.start()] if m else rest)


def _all(text: str, needed: list[str] | tuple[str, ...], where: str) -> None:
    missing = [n for n in needed if n not in text]
    assert not missing, f"{where}: missing {missing}"


def _skill_md() -> str:
    return _read(SKILL / "SKILL.md")


def _step_raw(number: int) -> str:
    """Procedure step `number` of SKILL.md as written."""
    proc = _section(_skill_md(), "## Procedure")
    m = re.search(rf"^{number}\. \*\*", proc, flags=re.M)
    assert m, f"SKILL.md: no procedure step {number}"
    rest = proc[m.start():]
    n = re.search(rf"^{number + 1}\. \*\*", rest, flags=re.M)
    return rest[:n.start()] if n else rest


def _step(number: int) -> str:
    """Procedure step `number` of SKILL.md, flattened, without backticks."""
    return _plain(_step_raw(number))


def _between(text: str, first: str, last: str | None) -> str:
    start = text.index(first)
    end = text.index(last, start) if last else len(text)
    return text[start:end]


def _before(text: str, earlier: str, later: str, where: str) -> None:
    assert earlier in text and later in text, f"{where}: missing {[t for t in (earlier, later) if t not in text]}"
    assert text.index(earlier) < text.index(later), f"{where}: {earlier[:60]!r} is not before {later[:60]!r}"


def _verified_files() -> list[Path]:
    return [SKILL / "SKILL.md"] + ([PLUGIN / "README.md"] if HAS_PLUGIN else [])


def _verified(path: Path) -> str:
    return _section(_read(path), "## What is and is not verified")


def _bullets(section: str) -> list[str]:
    """The top-level bullets of a section, each as one line (continuation lines joined)."""
    bullets: list[str] = []
    for line in section.splitlines():
        if line.startswith("- "):
            bullets.append(line[2:].strip())
        elif line.startswith("  ") and line.strip() and bullets:
            bullets[-1] += " " + line.strip()
    return bullets


def _build_skill() -> str:
    return _plain(_read(PLUGIN / "skills" / "build" / "SKILL.md"))


# ----------------------------------------------------------------------------- T-1, T-2 [C-sol-8]

def test_p1_step_1_states_the_sample_header_of_each_column() -> None:
    """T-1: a new sub-bullet of P1 step 1, after the bullet on GoogleSQL reserved words."""
    step = _step(1)
    first = _between(step, " 1. Propose one contract per raw table", " 2. Fix in the proposed JSON")
    _before(first, "GoogleSQL reserved word", T1, "SKILL.md P1 step 1")
    _before(first, T1, "A destination column may not have the name of a helper column", "SKILL.md P1 step 1")


def test_p2_states_the_header_line_land_accepts_and_keeps_the_refusal_rule() -> None:
    """T-2: a new bullet of P2, after the land --csv bullet; the rule for a land refusal stays as it was."""
    step = _step(2)
    _before(step, "ROTM land MODEL --table <t> --csv <extract.csv> checks the extract against the contract", T2,
            "SKILL.md P2")
    _before(step, T2, "A new extract whose source total changed", "SKILL.md P2")
    _all(step, LAND_REFUSAL_RULE, "SKILL.md P2, the rule for a land refusal")
    assert "(column names and order," not in step, "SKILL.md P2 still says the header must be the names in order"


# ----------------------------------------------------------------------------- T-3 [J-2, J-3, J-4] part (3)

def test_step_0_says_raw_holds_only_the_snapshots_land_writes() -> None:
    step = _step(0)
    assert step.rstrip().endswith(T3_STEP0), f"SKILL.md step 0: the last bullet is not T-3: {step[-300:]}"


def test_p2_states_lands_note_and_the_snapshot_path_given_to_csv() -> None:
    step = _step(2)
    _before(step, T2, T3_P2, "SKILL.md P2")
    _before(step, T3_P2, "A new extract whose source total changed", "SKILL.md P2")


# ----------------------------------------------------------------------------- T-4 [A-aera-2, C-sol-5]

def test_step_8_gives_the_refresh_command_with_strict_and_the_x_no_query_sentence() -> None:
    step = _step(8)
    _all(step, [T4_STEP_1, T4_STEP_5, T4_SUB], "SKILL.md step 8")
    _before(step, T4_STEP_1, T4_SUB, "SKILL.md step 8.1")
    _before(step, T4_SUB, " 2. More raw rows than helper rows", "SKILL.md step 8.1")
    commands = re.findall(r"`ROTM check MODEL --refresh[^`]*`", _step_raw(8))
    assert commands, "SKILL.md step 8 gives no refresh command"
    missing = [c for c in commands if "--strict" not in c]
    assert not missing, f"SKILL.md step 8: refresh commands without --strict: {missing}"


def test_step_8_states_the_read_back_at_accept_refresh() -> None:
    step = _step(8)
    _all(step, [T4_READ_BACK], "SKILL.md step 8")
    found = [t for t in T4_OLD if t in _plain(_skill_md())]
    assert not found, f"SKILL.md keeps the v6 sentences on the read-back: {found}"


# ----------------------------------------------------------------------------- T-5 [A-aera-4, A-aera-13]

def test_p5_states_the_per_cell_ratio_check_and_w_check_guard() -> None:
    step = _step(5)
    _all(step, [T5_COUNT, T5_RATIO, T5_GUARD], "SKILL.md P5")
    _before(step, T5_COUNT, T5_RATIO, "SKILL.md P5")
    assert T5_OLD not in step, "SKILL.md P5 keeps the v6 bullet on ratio blocks"


# ----------------------------------------------------------------------------- T-6 [A-aera-8]

def test_inputs_product_bullet_states_functions_only_google_sheets_has() -> None:
    inputs = _plain(_section(_skill_md(), "## Inputs"))
    _before(inputs, "Product: excel, sheets or both", T6_INPUTS, "SKILL.md Inputs")
    _before(inputs, T6_INPUTS, "A model folder: one folder per model", "SKILL.md Inputs")


def test_p7_step_2_states_x_sheets_only() -> None:
    step = _step(7)
    second = _between(step, " 2. ROTM check MODEL --strict recalculates", " 3. ROTM render")
    _all(second, [T6_P7], "SKILL.md P7 step 2")


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_not_verified_lists_a_model_with_functions_only_google_sheets_has(path: Path) -> None:
    not_verified = _plain(_section(_verified(path), "### Not verified"))
    _all(not_verified, [T6_NOT_VERIFIED], f"{path.name}, Not verified")


# ----------------------------------------------------------------------------- T-7 [J-1]

def test_p3_points_to_the_rank_pattern() -> None:
    _all(_step(3), [T7], "SKILL.md P3")
    assert (SKILL / "references" / "helper-patterns.md").is_file()


# ----------------------------------------------------------------------------- T-8 [J-2, J-3, J-4] part (2)

def test_p7_step_3_names_the_list_files_of_render_json_and_its_fields() -> None:
    third = _between(_step(7), " 3. ROTM render", " 4. Without Excel")
    _all(third, [T8, "A render covers rows 1 to 200 and columns A to AN (40 columns) of each sheet."],
         "SKILL.md P7 step 3")
    _before(third, T8, "--max-rows <last_row> --max-cols <last_col>", "SKILL.md P7 step 3")


def test_render_line_quoted_in_skill_md_is_the_one_excel_py_prints() -> None:
    """The line SKILL.md quotes is the line excel.run_render prints for a cut sheet (excel.py is not changed)."""
    source = _read(SCRIPTS / "lib" / "excel.py")
    joined = re.sub(r'"\s*\\\s*\n\s*f?"', "", source)       # adjacent string parts joined
    m = re.search(r'cut = f"; (the sheet\'s used range ends at row \{i\.last_row\}, column \{i\.last_col\}: '
                  r'[^"]*)"', joined)
    assert m, "excel.py: the line run_render prints for a cut sheet was not found"
    printed = m.group(1).replace("{i.last_row}", "<last_row>").replace("{i.last_col}", "<last_col>")
    assert printed == RENDER_LINE, (printed, RENDER_LINE)


def test_render_index_holds_the_fields_skill_md_names(tmp_path: Path) -> None:
    """render.json (excel._write_render_index) holds the list files with sheet, file, truncated, last_row and
    last_col; no Excel is needed to write the index."""
    from lib import excel
    workbook = tmp_path / "book.xlsx"
    workbook.write_bytes(b"not read")
    out = tmp_path / "render"
    out.mkdir()
    items = [excel.RenderedSheet("long", out / "long.png", range="A1:C200", last_row=300, last_col=3,
                                 truncated=True, width_px=10, height_px=10),
             excel.RenderedSheet("short", out / "short.png", range="A1:B5", last_row=5, last_col=2,
                                 truncated=False, width_px=10, height_px=10)]
    excel._write_render_index(out, workbook, items, all_sheets=True)
    index = json.loads((out / "render.json").read_text(encoding="utf-8"))
    entries = {e["sheet"]: e for e in index["files"]}
    assert set(entries) == {"long", "short"}
    for field in ("sheet", "file", "truncated", "last_row", "last_col"):
        assert all(field in e for e in entries.values()), field
    assert entries["long"]["truncated"] is True and entries["long"]["last_row"] == 300
    assert entries["long"]["last_col"] == 3 and entries["long"]["file"] == "long.png"
    assert entries["short"]["truncated"] is False


def _rotm_excel(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("ROTM_NO_EXCEL", None)
    return subprocess.run(ROTM + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=900)


@pytest.mark.excel
def test_render_of_a_sheet_of_300_rows_names_its_last_row_and_column(tmp_path: Path) -> None:
    """[J-2, J-3, J-4] part (2): rotm.py render of a workbook with a sheet of 300 rows (columns A to C) prints for
    that sheet a line that says the sheet was cut, with row 300 and column 3, and render.json holds its entry in
    files with truncated true, last_row 300 and last_col 3; a sheet inside the render gets neither."""
    from openpyxl import Workbook
    wb = Workbook()
    long = wb.active
    long.title = "long"
    for r in range(1, 301):
        for c in range(1, 4):
            long.cell(r, c, f"Col {c}" if r == 1 else r * c)
    short = wb.create_sheet("short")
    for r in range(1, 6):
        short.cell(r, 1, r)
        short.cell(r, 2, r * 2)
    path = tmp_path / "cut.xlsx"
    wb.save(path)
    out = tmp_path / "render"
    proc = _rotm_excel("render", str(path), "--out", str(out))
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    lines = proc.stdout.splitlines()
    long_line = [ln for ln in lines if ln.startswith("note render long:")]
    short_line = [ln for ln in lines if ln.startswith("note render short:")]
    assert len(long_line) == 1 and len(short_line) == 1, lines
    expected = RENDER_LINE.replace("<last_row>", "300").replace("<last_col>", "3")
    assert long_line[0].endswith("; " + expected), long_line[0]
    assert "used range ends at row 300" in long_line[0]
    assert "used range ends" not in short_line[0], short_line[0]
    index = json.loads((out / "render.json").read_text(encoding="utf-8"))
    entries = {e["sheet"]: e for e in index["files"]}
    assert entries["long"]["truncated"] is True, entries["long"]
    assert entries["long"]["last_row"] == 300 and entries["long"]["last_col"] == 3, entries["long"]
    assert entries["long"]["file"] == "long.png" and (out / "long.png").is_file()
    assert entries["short"]["truncated"] is False, entries["short"]


# ----------------------------------------------------------------------------- T-9 [A-aera-11, A-aera-15]

def test_p6_states_what_the_audit_does_with_conditional_formats() -> None:
    step = _step(6)
    bullet = _between(step, "The audit compares six style attributes", "Raw columns show")
    _all(bullet, [T9], "SKILL.md P6, the bullet on the six style attributes")


# ----------------------------------------------------------------------------- T-10 [A-aera-3, C-sol-2]

def test_p2_states_the_kept_copies_of_a_workbook_with_a_live_query() -> None:
    step = _step(2)
    _before(step, "A rebuild over a query attached by hand", T10, "SKILL.md P2")
    _before(step, T10, "When land refuses an extract", "SKILL.md P2")
    _before(step, T10, T10_GUARD, "SKILL.md P2")


def test_the_kept_copy_statement_agrees_with_the_hooks_table() -> None:
    """P8's sentence on the raw-sheet guard is the rule the hooks table states."""
    hooks = _plain(_section(_skill_md(), "## Hooks (when the plugin is installed)"))
    row = hooks.split("| raw-sheet guard |", 1)[1].split("| audit on write |", 1)[0]
    assert "a copy over an existing workbook" in row and "are refused" in row, row


# ----------------------------------------------------------------------------- T-11 [J-2, J-3, J-4] part (1)

def test_p7_step_3_states_i_text_width() -> None:
    third = _between(_step(7), " 3. ROTM render", " 4. Without Excel")
    _all(third, [T11], "SKILL.md P7 step 3")


# ----------------------------------------------------------------------------- T-12 and the rule ids

@needs_plugin
def test_build_skill_states_the_new_findings_and_the_kept_copies() -> None:
    text = _build_skill()
    _all(text, [T12_GUARD, T12_WIDTH, T12_SHEETS, T10, T10_GUARD], "skills/build/SKILL.md")
    exit_1 = _between(text, "Exit 1: built with warnings (", "Fix and build again.")
    assert "W-CHECK-GUARD" in exit_1, f"skills/build/SKILL.md: the Exit 1 list does not name W-CHECK-GUARD: {exit_1}"
    _before(text, "first build with no query table yet writes static values and warns W-PQ-NOT-ATTACHED", T10,
            "skills/build/SKILL.md")


def test_skill_md_names_the_new_rule_ids() -> None:
    text = _plain(_skill_md())
    missing = [r for r in NEW_RULE_IDS if r not in text]
    assert not missing, f"SKILL.md does not name {missing}"


# ----------------------------------------------------------------------------- [J-6] what is and is not verified

# v8 (r7 verdict [J-2]): the subsection "Runs on the final v7 files" was replaced by "Runs on the final v8 files";
# v9 (r8 verdict [J-2]): that one by "Runs on the final v9 files". The tests of the v9 subsection (the integrator's
# line or the dated runs, the required runs, the v9 items and the integrator's sentences) are in test_v9_docs.py.
# What still holds of v7 is tested here.

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_the_v7_runs_subsection_is_replaced_by_the_v9_one(path: Path) -> None:
    section = _verified(path)
    assert RUNS_V7 not in section, f"{path.name}: the v7 subsection is left"
    assert P_RUNS_V7 not in section and VERSION_V7 not in section, f"{path.name}: the v7 line or version is left"
    for old in ("v3", "v4", "v5", "v6"):
        assert f"### Runs on the final {old} files" not in section, f"{path.name}: the {old} subsection is left"
    assert RUNS_V8 not in section, f"{path.name}: the v8 subsection is left"
    assert RUNS_V9 in section, f"{path.name}: no subsection '{RUNS_V9}'"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_no_v6_run_statement_is_carried_over(path: Path) -> None:
    text = _flat(_verified(path))
    found = [t for t in V6_RUN_TEXTS if t in text]
    assert not found, f"{path.name}: v6 run statements left: {found}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_verified_list_names_the_v7_items_with_their_tests(path: Path) -> None:
    """v8: the four v7 items stay in the verified list with their tests (the tests did not change in v8) and without
    the integrator's sentence; Google Sheets only under 'Not verified'."""
    section = _verified(path)
    verified_text = _section(section, "### Verified, with the tests that show it")
    verified = _flat(verified_text)
    not_verified = _flat(_section(section, "### Not verified"))
    bullets = _bullets(verified_text)
    present = [item for item in V7_VERIFIED if item in verified]
    assert present == list(V7_VERIFIED), f"{path.name}: v7 items missing: {set(V7_VERIFIED) - set(present)}"
    for item in present:
        own = [b for b in bullets if b.startswith(item)]
        assert len(own) == 1, f"{path.name}: {len(own)} bullets start with the v7 item '{item}'"
        missing = [t for t in V7_VERIFIED[item] if t not in own[0]]
        assert not missing, f"{path.name}: the verified item '{item}' does not name {missing}"
        assert KEEP not in own[0], f"{path.name}: the v7 item '{item}' still holds the integrator's sentence"
    assert "Google" not in verified, f"{path.name}: a Google Sheets statement in the verified list"
    _all(not_verified, ["Google Sheets", "not in Google Sheets"], f"{path.name}, Not verified")


@needs_plugin
def test_skill_md_and_readme_list_the_same_v7_items() -> None:
    def titles(path: Path) -> list[str]:
        verified = _flat(_section(_verified(path), "### Verified, with the tests that show it"))
        return [item for item in V7_VERIFIED if item in verified]
    assert titles(SKILL / "SKILL.md") == titles(PLUGIN / "README.md")

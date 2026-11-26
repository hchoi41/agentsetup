"""Tests of the v8 documents (package P8 of the v8 round, 2026-11-26). Owner: P8.

Each test reads the place where the v8 plan (section 5.4) puts a text and checks that the text is there, in words
that do not depend on line breaks (whitespace is made uniform and code-span backticks are removed):

- T-1 ([A-aera-8, C-sol-2]): SKILL.md P2: a record of only delimiters, an open quote, other quoting errors.
- T-2 ([A-aera-4]): SKILL.md P2, the header-line bullet: a sample header that is another column's name.
- T-3 ([C-sol-4]): SKILL.md P2, a rebuild over a query attached by hand: the queries it reads (full form, or the
  reduced form if P3 reports it), and the fix when Excel ran.
- T-4 ([A-aera-5, A-aera-6] part (2)): SKILL.md P5: W-CHECK-ROUND.
- T-5 ([C-sol-1]): SKILL.md P5, the D-CRITERIA bullet: a label that Excel does not match with itself.
- T-6 ([A-aera-5, A-aera-6] part (1)): SKILL.md "Common mistakes": V-INPUT-RANGE.
- T-7 ([A-aera-1]): SKILL.md step 8: no acceptance after a later landing (the sub-bullet after "Changed rows are
  accepted only when ...").
- T-8 ([C-sol-5]): the hooks tables of SKILL.md and README.md, row "finish check": the remembered disclosure.
- T-9 ([A-aera-18]): SKILL.md "Commands": the selftest progress lines.
- T-10 ([A-aera-13, C-sol-8]): "Not verified" of both files: the redirect with Find and replace in Google Sheets.
- [J-2]: SKILL.md and README.md "What is and is not verified": the version line, the subsection "Runs on the final
  v8 files" (the integrator's line, or the integrator's dated runs that name the required runs), no v7 run statement,
  the four v8 items with their tests (each test exists) and the integrator's sentence (before the runs every v8 item
  has it, after them none), every Google Sheets statement under "Not verified". test_v2_docs.py compares the runs of
  the two files.
- v9 (r8 verdict [J-2] and the texts D-4 and D-8 of the v9 plan): the subsection became "Runs on the final v9
  files"; test_v9_docs.py tests it, the required runs and the v9 items. The tests here keep what still holds of v8:
  the v8 subsection, line, version and head bullets are gone, no v7 run text, the four v8 items with their tests and
  without the integrator's sentence; T-5 was replaced by D-4 and T-8 is followed by D-8.

The rule ids these documents name come from the code of other packages (P3 build.py, P4 check.py and audit.py);
test_references.py::test_rule_ids_mentioned_exist checks the references against the code (plan section 7, I-1). The
tests the v8 items name are written by P2, P3, P4 and P5; test_v8_verified_items_name_their_tests fails until they
exist.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v8_docs.py -q -p no:cacheprovider
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")

# ----------------------------------------------------------------------------- the texts of plan section 5.4

# T-1 with the wording of requests v8_P5 R-1 and v8_P8 R2 (integrator): L-NULL of the first column refuses a record
# of only delimiters; L-KEY names it only when two such records repeat a key; another number of fields is L-FIELDS.
T1 = ("land --csv skips only a physically blank line. A record that holds only delimiters (every field empty, for "
      "example ,,) is a row like any other: the null rule of the first column refuses it (L-NULL; L-KEY also names "
      "it when two such records repeat a key; a record with another number of fields than the header is L-FIELDS), "
      "the finding names its CSV line, and nothing is written; remove such records from the extract. A quoted field "
      "whose closing quote is missing at "
      "the end of the file is refused with the line where the field starts, and any other quoting error with its "
      "line; nothing is written. contract propose counts the records of only delimiters in a sample and names their "
      'lines under "Points to confirm".')
T2 = ("A contract in which one column's sample header (csv_header) is another column's destination name is refused "
      "by contract show, contract agree (exit 2, nothing recorded) and land (nothing written), because a header line "
      "could be read either way. Rename that destination column, or remove the csv_header entries, then run contract "
      "show and get the contract agreed again. contract propose never writes such a contract.")
T3_FULL = ("A query attached by hand that reads other workbook queries by name (a staging query, directly or through "
           "others): build keeps the M text of each of them in queries/<name>.hand.pq.m, adds them again as "
           "connection-only queries before it attaches the raw query, and names them in the report. A query it reads "
           "that cannot be made again (for example one that reads an Excel table of the workbook) is named in the "
           "refusal (exit 2, nothing written).")
T3_REDUCED = ("A query attached by hand that reads another workbook query is not carried over: the rebuild is refused "
              "(exit 2, nothing written), the message names the query it reads, and a query attached by hand is "
              "carried over only when it holds all its steps itself.")
T3_BOTH = ("Without Excel, run the build on a PC with Excel, or pass --drop-hand-queries. When Excel ran and a query "
           "cannot be carried over, the refusal gives --drop-hand-queries and says that the present workbook is kept "
           "until then.")
T3_OLD = "the message names the query and the sheet. Run the build on a PC with Excel, or pass"
# T-4 with the fields build reads (request v8_P3 R3, integrator) and the newer names of the same functions.
T4 = ("A check expression (actual, expected, min, max or bound) that holds ROUND, ROUNDUP, ROUNDDOWN, MROUND, INT, "
      "TRUNC, CEILING or FLOOR (also CEILING.MATH, CEILING.PRECISE, ISO.CEILING, FLOOR.MATH, FLOOR.PRECISE) makes "
      "build warn W-CHECK-ROUND (strict exit 1), and audit with a manifest reports "
      "W-CHECK-ROUND at the check cell: a check compares the unrounded difference with the tolerance "
      "(references/checks.md, rule 3). When a business rule needs rounded values, round them in a block.")
T4_AFTER = "A check expression (actual or expected) that holds IFERROR or IFNA makes build warn W-CHECK-GUARD"
T5 = ("A label that Excel's criteria matching does not match with itself (the texts TRUE and FALSE in any case, which "
      "Excel reads as logical values; a label from a cell that starts with =, < or >) leaves the rows with that label "
      "out of the sum. Build and check warn D-CRITERIA with that reason and name the label and the first raw row; use "
      "a helper key with a fixed prefix on both sides (references/helper-patterns.md, \"A label that Excel's criteria "
      'matching does not match with itself").')
# v9: the start and the end of the text that replaced T-5 (D-4 of the v9 plan).
T5_V9_START = "A label that Excel's criteria matching does not match with itself (for example the texts TRUE and FALSE"
T5_V9_END = ('(references/helper-patterns.md, "A label that Excel\'s criteria matching does not match with '
             'itself").')
T5_AFTER = ("Build and check warn D-CRITERIA and name both values; make the labels unambiguous "
            "(references/helper-patterns.md, \"Labels that Excel's criteria matching merges\").")
T6 = ("check (after recalculation, and on the saved values without Excel) and audit (with a manifest) read each scalar "
      "input that declares a valid range or valid values from the workbook: a saved value outside it is V-INPUT-RANGE "
      "(error), which names the cell, the value and the range, and the input's note when it has one. No row is added "
      "to the checks sheet.")
T7 = ("When raw/<t>.csv was landed after rotm.py last wrote rows into the workbook (the manifest's landed digest "
      "differs from its workbook digest, for example after land --force), check --accept-refresh accepts no changed "
      "rows, whatever raw_<t> holds: R-CHANGED says that raw/<t>.csv was landed after the workbook was last written, "
      "and raw/<t>.csv, raw/<t>.controls.json and the manifest stay byte for byte. Rebuild from the landed snapshot "
      "(ROTM build MODEL --force), then refresh (ROTM check MODEL --refresh --strict) and accept.")
T7_AFTER = "Changed rows are accepted only when they differ from the rows the workbook held"
T8 = ('A stop let through because the last message says "not recalculated" is remembered for each such workbook\'s '
      "sha256: a later stop in the same session does not refuse again for a workbook unchanged since that message; a "
      "workbook written after it is judged again.")
T9 = ("selftest writes one line to stderr when each step starts and when it ends (the step name and the elapsed "
      "time), and about every 5 minutes during the pytest step (with the count of tests done when pytest's output "
      "gives it); stdout and the JSON report are as before.")
T9_AFTER = "selftest (the plugin's own tests)."
T10 = ("The redirect of the formulas with Find and replace in Google Sheets (references/google-sheets.md, \"Cutover "
       'when the extract is on a new sheet"): not run in Google Sheets.')
# README.md names the reference by its path from the plugin folder, as its other items do.
T10_README = T10.replace("(references/", "(skills/rule-of-thumb-model/references/")
V8_RULE_IDS = ("W-CHECK-ROUND", "V-INPUT-RANGE")

# ----------------------------------------------------------------------------- [J-2] (plan sections 5.3 and 5.5)

RUNS_V8 = "### Runs on the final v8 files"
P_RUNS_V8 = "Integrator: write here the runs made on the final v8 files (plan section 8)."
KEEP = "Integrator: keep this item only when these tests passed in the final runs."
VERSION_V8 = "Version v8 of 2026-11-26."
HEAD_V8 = ["The runs made on the final v8 files are listed in the first subsection.",
           "An item of the verified list holds for v8 only when the tests it names passed in one of those runs."]
# v9 (r8 verdict [J-2]): the subsection that replaces RUNS_V8 (test_v9_docs.py checks its contents).
RUNS_V9 = "### Runs on the final v9 files"
# The four v8 items and their fixed tests (plan 5.3); each item gives one or more lists of tests, one of which it
# names: the query item names the full-form test or the reduced-form test, and the self-contained test in both.
V8_VERIFIED = {
    "The monthly roll check of rolling_forecast (v8)": [[
        "test_v8_templates.py::test_rolling_forecast_shipped_inputs_hold_every_check",
        "test_v8_templates.py::test_rolling_forecast_roll_without_new_rows_fails_the_landed_months_check",
        "test_v8_templates.py::test_rolling_forecast_roll_with_new_rows_holds_and_shows_the_closed_month",
        "test_v8_templates.py::test_rolling_forecast_closed_month_without_sales_passes_with_the_input_set"]],
    "Records of only delimiters and open quotes in a CSV (v8)": [[
        "test_v8_land.py::test_record_of_only_delimiters_is_refused_with_its_line",
        "test_v8_land.py::test_blank_line_is_skipped_as_before",
        "test_v8_land.py::test_open_quote_at_the_end_is_refused_with_its_line",
        "test_v8_land.py::test_csv_in_excel_form_lands",
        "test_v8_land.py::test_shipped_folders_land_with_the_same_digests"]],
    "Saved inputs outside their valid range (v8)": [[
        "test_v8_check.py::test_saved_scale_outside_its_range_fails_check_and_audit",
        "test_v8_check.py::test_shipped_models_give_no_input_range_finding"]],
    "A query attached by hand that reads another query (v8)": [
        ["test_v8_build.py::test_rebuild_carries_a_staging_query_a_hand_query_reads",
         "test_v8_build.py::test_self_contained_hand_query_is_carried_as_in_v7"],
        ["test_v8_build.py::test_rebuild_refuses_a_hand_query_that_reads_another_query",
         "test_v8_build.py::test_self_contained_hand_query_is_carried_as_in_v7"]],
}
# Numbers and words of the v7 runs that v8 must not carry ([J-2]: do not carry over the v7 numbers).
V7_RUN_TEXTS = ("3,022", "197 skipped", "2,574", "578 skipped", "188 passed", "09:49 to 10:02", "10:53 to 11:03",
                "09:39 to 10:52", "09:49 to 09:58", "09:36 to 09:39", "09:22:41", "09:16 to 09:17", "337 files",
                "checks 26, 13 and 17", "the 48 test files", "Version v7", "on the final v7 files", "for v7 only",
                "Runs on the final v7 files", "Integrator: replace this line with the runs on the final v7 files.")
# v9: the runs the integrator states are required in test_v9_docs.py (REQUIRED_RUNS there).


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


def _before(text: str, earlier: str, later: str, where: str) -> None:
    assert earlier in text and later in text, f"{where}: missing {[t for t in (earlier, later) if t not in text]}"
    assert text.index(earlier) < text.index(later), f"{where}: {earlier[:60]!r} is not before {later[:60]!r}"


def _between(text: str, first: str, last: str | None) -> str:
    start = text.index(first)
    end = text.index(last, start) if last else len(text)
    return text[start:end]


def _skill_md() -> str:
    return _read(SKILL / "SKILL.md")


def _step(number: int) -> str:
    """Procedure step `number` of SKILL.md, flattened, without backticks."""
    proc = _section(_skill_md(), "## Procedure")
    m = re.search(rf"^{number}\. \*\*", proc, flags=re.M)
    assert m, f"SKILL.md: no procedure step {number}"
    rest = proc[m.start():]
    n = re.search(rf"^{number + 1}\. \*\*", rest, flags=re.M)
    return _plain(rest[:n.start()] if n else rest)


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


def _hooks_row(path: Path, hook: str) -> str:
    """The row of `hook` in the hooks table of SKILL.md or README.md, flattened, without backticks."""
    heading = "## Hooks" + (" (when the plugin is installed)" if path.name == "SKILL.md" else "")
    lines = [ln for ln in _section(_read(path), heading).splitlines() if ln.startswith(f"| {hook} |")]
    assert len(lines) == 1, f"{path.name}: {len(lines)} rows for the hook '{hook}'"
    return _plain(lines[0])


def _named_tests(text: str) -> list[tuple[str, str]]:
    """The tests a text names, as (file, function): `file.py::test_x`, and `::test_y` after it for the same file."""
    found: list[tuple[str, str]] = []
    current = ""
    for m in re.finditer(r"(test_\w+\.py)?::(test_\w+)", text):
        current = m.group(1) or current
        if current:
            found.append((current, m.group(2)))
    return found


def _test_exists(file: str, name: str) -> bool:
    path = TESTS / file
    return path.is_file() and re.search(rf"^def {re.escape(name)}\(", _read(path), flags=re.M) is not None


# ----------------------------------------------------------------------------- T-1, T-2 [A-aera-8, C-sol-2], [A-aera-4]

def test_p2_states_the_record_of_only_delimiters_and_the_quoting_refusals() -> None:
    """T-1: a new bullet of P2 after the land --csv bullet and before the header-line bullet."""
    step = _step(2)
    _before(step, "ROTM land MODEL --table <t> --csv <extract.csv> checks the extract against the contract", T1,
            "SKILL.md P2")
    _before(step, T1, "The header line land --csv accepts", "SKILL.md P2")


def test_p2_header_line_bullet_refuses_a_sample_header_that_is_another_columns_name() -> None:
    """T-2: appended to the header-line bullet of P2 (after its last v7 sentence, before the next bullet)."""
    step = _step(2)
    bullet = _between(step, "The header line land --csv accepts", "land prints a note that lists the files")
    _all(bullet, [T2], "SKILL.md P2, the header-line bullet")
    _before(bullet, "its CSV needs the destination names.", T2, "SKILL.md P2, the header-line bullet")


# ----------------------------------------------------------------------------- T-3 [C-sol-4]

def test_p2_states_what_a_rebuild_does_with_the_queries_a_hand_query_reads() -> None:
    """T-3: the full form (or, if P3 reports the reduced form, the reduced form) in the sub-bullet "With Excel"; the
    sentence on the ways on in the sub-bullet "Without Excel"; the v7 sentence is gone."""
    step = _step(2)
    rebuild = _between(step, "A rebuild over a query attached by hand", "A rebuild of a workbook whose raw sheet")
    with_excel = _between(rebuild, "With Excel:", "Without Excel, or when the M text")
    forms = [t for t in (T3_FULL, T3_REDUCED) if t in with_excel]
    assert len(forms) == 1, f"SKILL.md P2, With Excel: {len(forms)} of the two forms of T-3: {with_excel}"
    without = _between(rebuild, "Without Excel, or when the M text", None)
    _all(without, [T3_BOTH, "--drop-hand-queries", "W-PQ-NOT-ATTACHED", "X-NO-QUERY"], "SKILL.md P2, Without Excel")
    assert T3_OLD not in step, "SKILL.md P2 keeps the v7 sentence that gives the PC with Excel first in every case"


# ----------------------------------------------------------------------------- T-4, T-5 [A-aera-5, A-aera-6], [C-sol-1]

def test_p5_states_w_check_round_after_w_check_guard() -> None:
    step = _step(5)
    _before(step, T4_AFTER, T4, "SKILL.md P5")
    _before(step, T4, "A check holds 0 (shown OK)", "SKILL.md P5")


def test_p5_criteria_bullet_states_a_label_that_excel_does_not_match_with_itself() -> None:
    """v9 ([A-aera-1, C-sol-2], text D-4 of the v9 plan): T-5 was replaced by the general rule with its third case;
    the D-CRITERIA bullet still ends with the rule for a label that does not match itself (test_v9_docs.py checks
    D-4 word for word)."""
    step = _step(5)
    assert T5 not in step, "SKILL.md P5 keeps the v8 text T-5 with its two cases only"
    _before(step, T5_AFTER, T5_V9_START, "SKILL.md P5, the D-CRITERIA bullet")
    assert step.rstrip().endswith(T5_V9_END), f"SKILL.md P5: the self-mismatch rule does not end the D-CRITERIA " \
                                              f"bullet: {step[-300:]}"


# ----------------------------------------------------------------------------- T-6 [A-aera-5, A-aera-6] part (1)

def test_common_mistakes_typing_into_the_inputs_sheet_states_v_input_range() -> None:
    mistakes = _plain(_section(_skill_md(), "## Common mistakes"))
    bullet = _between(mistakes, "**Typing new values into the inputs sheet.**", "**Appending rows to")
    _all(bullet, [T6], "SKILL.md Common mistakes, Typing new values into the inputs sheet")
    _before(bullet, "with --force the edited file is kept as model.bak_<time>.xlsx", T6,
            "SKILL.md Common mistakes, Typing new values into the inputs sheet")


# ----------------------------------------------------------------------------- T-7 [A-aera-1]

def test_step_8_refuses_the_acceptance_after_a_later_landing() -> None:
    """T-7: a new sub-bullet of step 8 right after the sub-bullet "Changed rows are accepted only when ..."."""
    step = _step(8)
    _before(step, T7_AFTER, T7, "SKILL.md step 8")
    _before(step, T7, "--refresh asked for and no refresh ran", "SKILL.md step 8")
    between = _between(step, T7_AFTER, T7)          # ends with the bullet mark of T-7 itself
    assert between.rstrip().endswith(" -") and between.count(" - ") == 1, \
        f"SKILL.md step 8: T-7 is not the sub-bullet right after '{T7_AFTER}': {between}"


# ----------------------------------------------------------------------------- T-8 [C-sol-5]

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_finish_check_row_states_the_remembered_disclosure(path: Path) -> None:
    """v9 ([C-sol-5], text D-8 of the v9 plan): the stale pass follows T-8 and ends the row (test_v9_docs.py)."""
    row = _hooks_row(path, "finish check")
    _all(row, [T8, "not recalculated"], f"{path.name} hooks table, finish check")
    rest = row.rstrip(" |")[row.index(T8) + len(T8):].strip()
    assert rest == "" or rest.startswith("A passing audit or check counts only while"), \
        f"{path.name} hooks table: T-8 is not followed by the end of the row or by the v9 stale pass: {rest[:120]}"


# ----------------------------------------------------------------------------- T-9 [A-aera-18]

def test_commands_section_states_the_selftest_progress_lines() -> None:
    commands = _plain(_section(_skill_md(), "## Commands"))
    _before(commands, T9_AFTER, T9, "SKILL.md Commands")
    _before(commands, T9, "ROTM <subcommand> -h lists every option.", "SKILL.md Commands")


# ----------------------------------------------------------------------------- T-10 [A-aera-13, C-sol-8]

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_not_verified_lists_the_redirect_with_find_and_replace(path: Path) -> None:
    not_verified = _plain(_section(_verified(path), "### Not verified"))
    _all(not_verified, [T10 if path.name == "SKILL.md" else T10_README], f"{path.name}, Not verified")


def test_skill_md_states_the_v8_rules() -> None:
    """The v8 rules SKILL.md states: W-CHECK-ROUND, V-INPUT-RANGE, the record of only delimiters, the finish check's
    remembered disclosure and the selftest progress lines (T-1, T-4, T-6, T-8, T-9)."""
    text = _plain(_skill_md())
    missing = [r for r in V8_RULE_IDS if r not in text]
    assert not missing, f"SKILL.md does not name {missing}"
    _all(text, [T1, T4, T6, T8, T9], "SKILL.md")


# ----------------------------------------------------------------------------- [J-2] what is and is not verified

# v9 (r8 verdict [J-2]): the subsection "Runs on the final v8 files" was replaced by "Runs on the final v9 files";
# the tests of the v9 subsection (the integrator's line or the dated runs, the required runs, the v9 items and the
# integrator's sentences) are in test_v9_docs.py. What still holds of v8 is tested here.

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_the_v8_runs_subsection_is_replaced_by_the_v9_one(path: Path) -> None:
    section = _verified(path)
    for old in ("v3", "v4", "v5", "v6", "v7", "v8"):
        assert f"### Runs on the final {old} files" not in section, f"{path.name}: the {old} subsection is left"
    assert P_RUNS_V8 not in section and VERSION_V8 not in section, f"{path.name}: the v8 line or version is left"
    left = [h for h in HEAD_V8 if h in _flat(section)]
    assert not left, f"{path.name}: the v8 head bullets are left: {left}"
    assert RUNS_V9 in section, f"{path.name}: no subsection '{RUNS_V9}'"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_no_v7_run_statement_is_carried_over(path: Path) -> None:
    text = _flat(_verified(path))
    found = [t for t in V7_RUN_TEXTS if t in text]
    assert not found, f"{path.name}: v7 run statements left: {found}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_v8_verified_items_name_their_tests(path: Path) -> None:
    """v9: the four v8 items stay in the verified list (the v8 integrator kept all four, the query item in its full
    form), each in its own bullet with one of its lists of fixed tests, each of which exists, and without the
    integrator's sentence."""
    section = _verified(path)
    verified_text = _section(section, "### Verified, with the tests that show it")
    verified = _flat(verified_text)
    bullets = _bullets(verified_text)
    present = [item for item in V8_VERIFIED if item in verified]
    assert present == list(V8_VERIFIED), f"{path.name}: v8 items missing: {set(V8_VERIFIED) - set(present)}"
    missing_tests: list[str] = []
    for item in present:
        own = [b for b in bullets if b.startswith(item)]
        assert len(own) == 1, f"{path.name}: {len(own)} bullets start with the v8 item '{item}'"
        lists = [tests for tests in V8_VERIFIED[item] if all(t in own[0] for t in tests)]
        assert lists, f"{path.name}: the verified item '{item}' names none of {V8_VERIFIED[item]}"
        assert KEEP not in own[0], f"{path.name}: the v8 item '{item}' still holds the integrator's sentence"
        named = _named_tests(own[0])
        assert named, f"{path.name}: the verified item '{item}' names no test"
        missing_tests += [f"{f}::{n}" for f, n in named if not _test_exists(f, n)]
    assert not missing_tests, f"{path.name}: the v8 items name tests that do not exist: {missing_tests}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_google_sheets_statements_stay_under_not_verified(path: Path) -> None:
    """[J-2]: every statement about Google Sheets is under 'Not verified': the rest of 'What is and is not verified'
    (the head, the runs and the verified list) does not name Google; 'Not verified' keeps the Google Sheets items."""
    section = _verified(path)
    not_verified = _section(section, "### Not verified")
    rest = section.replace(not_verified, "")
    assert "Google" not in rest, f"{path.name}: a Google Sheets statement outside 'Not verified': " \
                                 f"{[b[:100] for b in _bullets(rest) if 'Google' in b]}"
    plain = _plain(not_verified)
    first = ("Anything in Google Sheets or Connected Sheets" if path.name == "SKILL.md"
             else "Google Sheets and Connected Sheets (no Google account was available)")
    _all(plain, [first, "not in Google Sheets", "GoogleSQL", T10 if path.name == "SKILL.md" else T10_README],
         f"{path.name}, Not verified")


@needs_plugin
def test_skill_md_and_readme_list_the_same_v8_items() -> None:
    def titles(path: Path) -> list[str]:
        verified = _flat(_section(_verified(path), "### Verified, with the tests that show it"))
        return [item for item in V8_VERIFIED if item in verified]
    assert titles(SKILL / "SKILL.md") == titles(PLUGIN / "README.md")

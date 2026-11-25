"""Tests of the v6 documents (package P8 of the v6 round, 2026-11-26). Owner: P8.

Each test reads the place where the v6 plan (section 5.4) puts a text and checks that the text is there:

- T-1, T-2 ([A-aera-3, C-sol-2]): SKILL.md "Common mistakes" and model-spec-format.md "Inputs": an input that
  scales displayed amounts is fixed, or every unit label reads it.
- T-3 ([A-aera-6]): model-spec-format.md "Argument counts": the 29 functions and the counts of plan section 5.1.
- T-4 ([A-aera-6]): the fix of X-OPEN in SKILL.md P7, checks.md and skills/check/SKILL.md names F-PARSE and
  F-ARGS; skills/audit/SKILL.md names F-ARGS.
- T-5 ([C-sol-3]): skills/audit/SKILL.md and checks.md: the audit reports H-BEYOND below the last raw row, and H-GAP
  and H-INCONSISTENT only up to it.
- T-10, T-11 ([C-sol-6, J-1]): an empty date shows as an empty cell (SKILL.md P3); I-HIDDEN-DECIMALS (SKILL.md
  "After a refresh", skills/check/SKILL.md).
- T-13 ([A-aera-2]): a query changed in Excel's editor is found at the next check --refresh (excel-power-query.md,
  SKILL.md "After a refresh", skills/check/SKILL.md).
- T-14 ([A-aera-1, C-sol-1]): the words that read as a refusal or a stop (the hooks tables of SKILL.md and
  README.md, SKILL.md P1 step 6, skills/contract/SKILL.md step 7, column-contract.md).
- T-15 ([A-aera-5]): skills/diff/SKILL.md: a cell whose stored type changed is listed with both types.
- [J-3]: SKILL.md and README.md "What is and is not verified": the subsection "Runs on the final v6 files", the
  four v6 items of the verified list with their tests (plan section 5.5), Google Sheets only under "Not verified".
- Tests whose names end in `_in_the_code` read a name or a text from the code of another package (P1 formula.py and
  excel.py, P2 audit.py, P3 check.py): they fail until that package has written it (plan section 7, I-3).

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v6_docs.py -q -p no:cacheprovider
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")

RUNS_V6 = "### Runs on the final v6 files"
P_RUNS_V6 = "Integrator: replace this line with the runs on the final v6 files."
KEEP_V6 = "Integrator: keep this item only when these tests passed in the final runs."
VERSION_V6 = "Version v6 of 2026-11-26"

# The four v6 items of the verified list and their tests (plan sections 5.3 and 5.5).
V6_VERIFIED = {
    "The strict audit after helper rows below a shorter extract are cleared by hand": [
        "test_v6_audit.py::test_shorter_extract_repair_passes_check_and_audit",
        "test_v6_audit.py::test_zero_row_repair_passes_check_and_audit",
        "test_v6_audit.py::test_helper_cell_emptied_inside_the_boundary_is_h_gap",
        "test_v6_audit.py::test_helper_formulas_below_the_last_raw_row_are_h_beyond",
        "test_v6_audit.py::test_refresh_and_extend_cycle_audits_strict"],
    "The query read back at refresh": [
        "test_v6_check.py::test_attach_records_the_formula_hash",
        "test_v6_check.py::test_query_changed_in_excel_is_found_at_refresh",
        "test_v6_check.py::test_unchanged_query_refresh_cycle_exits_0",
        "test_v6_check.py::test_manifest_without_formula_hash_gets_a_note"],
    "The argument-count rule": [
        "test_v6_formula.py::test_if_with_four_arguments_is_f_args",
        "test_v6_formula.py::test_listed_functions_pass_at_their_smallest_and_largest_count",
        "test_v6_formula.py::test_build_refuses_if_with_four_arguments_at_its_json_path",
        "test_v6_formula.py::test_audit_strict_reports_f_args_at_the_cell",
        "test_v6_formula.py::test_shipped_and_foreign_formulas_give_no_f_args"],
    "The number-format comparison": [
        "test_v6_audit.py::test_quoted_percent_sign_is_s_numfmt",
        "test_v6_audit.py::test_escaped_hyphens_in_a_date_format_are_equal",
        "test_v6_audit.py::test_theme_number_formats_after_an_excel_save_leaves_no_residual",
        "test_v6_audit.py::test_shipped_models_audit_strict"],
}

# Numbers and words of the v5 runs that v6 must not carry (J-3).
V5_RUN_TEXTS = ("2,609", "2,255", "151 skipped", "455 skipped", "141 passed", "00:49 to 00:56", "00:42 to 00:48",
                "00:24 to 00:26", "00:49 to 00:54", "00:56 to 01:03", "23:28 to", "23:20 to 23:24",
                "23:24 to 23:26", "Version v5", "on the final v5 files", "for v5 only")

# The runs the integrator states (verdict [J-3]; plan section 8, step 5).
REQUIRED_RUNS = ("selftest --quick", "f02_skill_alone", "f27_power_query", "-m excel", "selftest --strict")

# The argument counts of plan section 5.1: (smallest, largest, parity, the <allowed> text of F-ARGS).
PAIRS_255 = "a range and then pairs of a criteria range and its criterion (3, 5, 7, ... up to 255)"
PAIRS_253 = "a range and then pairs of a criteria range and its criterion (3, 5, 7, ... up to 253)"
PAIRS_254 = "pairs of a criteria range and its criterion (2, 4, 6, ... up to 254)"
ARG_COUNTS = {
    "IF": (2, 3, None, "2 or 3"),
    **{n: (2, 2, None, "exactly 2") for n in ("IFERROR", "IFNA", "ROUND", "ROUNDUP", "ROUNDDOWN", "MOD", "COUNTIF",
                                              "EOMONTH", "EDATE")},
    "AND": (1, 255, None, "1 to 255"), "OR": (1, 255, None, "1 to 255"),
    **{n: (1, 1, None, "exactly 1") for n in ("NOT", "ABS", "INT", "YEAR", "MONTH", "DAY")},
    **{n: (2, 3, None, "2 or 3") for n in ("SUMIF", "AVERAGEIF", "MATCH")},
    "INDEX": (2, 4, None, "2 to 4"),
    "XLOOKUP": (3, 6, None, "3 to 6"),
    "DATE": (3, 3, None, "exactly 3"),
    "SUMIFS": (3, 255, "odd", PAIRS_255), "AVERAGEIFS": (3, 255, "odd", PAIRS_255),
    "MAXIFS": (3, 253, "odd", PAIRS_253), "MINIFS": (3, 253, "odd", PAIRS_253),
    "COUNTIFS": (2, 254, "even", PAIRS_254),
}

T1 = ["A display scale that the user can change while the unit labels are fixed text.",
      "An input that scales displayed amounts is fixed, or every unit label reads it.",
      'A scale from 1 to 1,000,000 under labels that say "KRW thousand" shows amounts under a wrong unit with every '
      "check passing.",
      'Give such an input "valid": {"min": 1000, "max": 1000}, the label "Display scale (fixed at 1000)" and a note '
      "that every unit label is in KRW thousand; the worked example and every shipped template do so."]
UNIT_RULE = "An input that converts a unit must apply to both sides of every comparison, or be fixed"
T2 = ("An input that scales displayed amounts is fixed (valid range 1000 to 1000, as the worked example and every "
      "shipped template have it), or every label and note that names the unit reads the input.")
T4 = ["The most likely cause is a formula Excel rejects.",
      "The strict audit finds two such causes: an incomplete formula (F-PARSE) and a listed function with too few or "
      'too many arguments (F-ARGS, references/model-spec-format.md "Argument counts").',
      "For another cause, open the workbook in Excel and read the message Excel shows."]
T5 = ["H-BEYOND", "below the last raw row",
      "H-GAP and H-INCONSISTENT only from the first helper row to the last raw row"]
T10 = ["date_or_empty", "datetime_or_empty", "1900-01-00", "null rule allowed, or agreed known nulls",
       "The fetched value is still 0.", "reads the null flag <column>_null"]
T11 = ["I-HIDDEN-DECIMALS (information; the exit code does not change): a number column of the new rows holds more "
       "decimals than its number format shows.",
       "The values are stored and calculated in full.",
       "A rebuild shows them when build chose the format; when the contract names the format, agree another format "
       "in the contract, then rebuild."]
T13 = ["A query changed in Excel's Power Query editor (or in any other way than rotm.py) is found at the next "
       "rotm.py check --refresh.",
       "query_formula_sha256", "line ends and trailing white space made uniform",
       "check --refresh reads the formula again through Excel before the refresh and compares.",
       "X-QUERY-CHANGED (a warning; an error with --accept-refresh), refreshes no table and accepts nothing",
       "raw/<t>.csv, raw/<t>.controls.json and the manifest stay as they were",
       "Put the change into queries/<t>.pq.m and run", "--table <t> --power-query",
       "A manifest written before v6 has no such hash: check --refresh notes that the workbook's query was not "
       "compared."]
T13_SHORT = ["X-QUERY-CHANGED", "Power Query editor", "query_formula_sha256", "refreshes no table and accepts nothing",
             "Put the change into queries/<t>.pq.m", "--table <t> --power-query", "before v6"]
T14 = ["reject, decline and refuse (also rejected, rejection, declined, refused, refusal)", "hold off", "hold on",
       "a negation before run, proceed, start, land, continue or go ahead", "do not run the query yet",
       "please wait", "wait until", "wait for", "no need to wait", "거절", "반려", "거부", "보류", "기다려", "하지 마"]
T14_NO_LIST = ["no list of words is required in an agreement", "need not name the table"]
T15 = ("A cell whose stored type changed (number, text, logical value) while its text reads the same is listed as "
       "changed, with both types.")


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


def _all(text: str, needed: list[str] | tuple[str, ...], where: str, *, lower: bool = False) -> None:
    hay = text.lower() if lower else text
    missing = [n for n in needed if (n.lower() if lower else n) not in hay]
    assert not missing, f"{where}: missing {missing}"


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


def _substep(step: str, number: int) -> str:
    """Sub-step `number` of a flattened procedure step (the text from ' <n>. ' to ' <n+1>. ')."""
    start = step.index(f" {number}. ")
    end = step.find(f" {number + 1}. ", start)
    return step[start:end if end > 0 else len(step)]


def _ref(name: str) -> str:
    return _plain(_read(REFS / name))


def _command_skill(name: str) -> str:
    return _plain(_read(PLUGIN / "skills" / name / "SKILL.md"))


def _verified_files() -> list[Path]:
    return [SKILL / "SKILL.md"] + ([PLUGIN / "README.md"] if HAS_PLUGIN else [])


def _verified(path: Path) -> str:
    return _section(_read(path), "## What is and is not verified")


def _hooks_row(path: Path, hook: str, next_hook: str) -> str:
    heading = "## Hooks" + (" (when the plugin is installed)" if path.name == "SKILL.md" else "")
    hooks = _plain(_section(_read(path), heading))
    return hooks.split(f"| {hook} |", 1)[1].split(f"| {next_hook} |", 1)[0]


def _run_bullets(section: str) -> list[str]:
    runs = _section(section, RUNS_V6)[len(RUNS_V6):]
    bullets: list[str] = []
    for line in runs.splitlines():
        if line.startswith("- "):
            bullets.append(line[2:].strip())
        elif line.startswith("  ") and line.strip() and bullets:
            bullets[-1] += " " + line.strip()
    return bullets


# ----------------------------------------------------------------------------- T-1, T-2 [A-aera-3, C-sol-2]

def test_display_scale_rule_in_common_mistakes() -> None:
    mistakes = _plain(_section(_skill_md(), "## Common mistakes"))
    _all(mistakes, T1 + [UNIT_RULE], "SKILL.md Common mistakes")


def test_display_scale_rule_in_the_specification_format_inputs() -> None:
    inputs = _plain(_section(_read(REFS / "model-spec-format.md"), "## Inputs"))
    _all(inputs, [T2, UNIT_RULE], "model-spec-format.md Inputs")


# ----------------------------------------------------------------------------- T-3 [A-aera-6] argument counts

def _argument_rows() -> list[list[str]]:
    section = _section(_read(REFS / "model-spec-format.md"), "## Argument counts")
    rows = []
    for line in section.splitlines():
        if line.startswith("| ") and not line.startswith("| Function") and not line.startswith("| ---"):
            rows.append([c.strip() for c in line.strip().strip("|").split(" | ")])
    return rows


def test_argument_counts_section_states_the_rules() -> None:
    section = _plain(_section(_read(REFS / "model-spec-format.md"), "## Argument counts"))
    _all(section, ["F-ARGS", "An empty argument counts", "IF(A1,,1) has 3", "A function outside the list is not "
                   "counted", "JSON path", "exit 2", "audit reports F-ARGS at the cell",
                   "Microsoft's function reference (support.microsoft.com"], "model-spec-format.md Argument counts")


def test_argument_counts_table_holds_the_29_functions_with_their_counts() -> None:
    found: dict[str, tuple[int, int, str | None, str]] = {}
    for cells in _argument_rows():
        assert len(cells) == 3, f"a row needs function, arguments allowed and the F-ARGS text: {cells}"
        names = re.findall(r"`([A-Z]+)`", cells[0])
        assert names, cells
        allowed = cells[1]
        m = re.match(r"exactly (\d+)", allowed) or re.match(r"(\d+) to (\d+)", allowed)
        assert m, f"no count in {allowed!r}"
        low, high = int(m.group(1)), int(m.group(m.lastindex))
        parity = "odd" if "odd" in allowed else "even" if "even" in allowed else None
        text = cells[2].strip("`")
        for name in names:
            assert name not in found, f"{name} is listed twice"
            found[name] = (low, high, parity, text)
    assert len(ARG_COUNTS) == 29
    assert found == ARG_COUNTS, {k: (found.get(k), v) for k, v in ARG_COUNTS.items() if found.get(k) != v}


def _call(name: str, n: int) -> str:
    return f"={name}({','.join(['A1'] * n)})"


def test_argument_counts_table_in_the_code() -> None:
    """The counts the table states are the counts formula.check_formula applies (package P1)."""
    from lib import formula
    wrong = []
    for name, (low, high, parity, text) in ARG_COUNTS.items():
        for n in (low, high):
            if any(i.rule == "F-ARGS" for i in formula.check_formula(_call(name, n))):
                wrong.append(f"{name} with {n}: F-ARGS")
        bad = [low - 1, high + 1] + ([low + 1] if parity else [])
        for n in bad:
            hits = [i for i in formula.check_formula(_call(name, n)) if i.rule == "F-ARGS"]
            if not hits:
                wrong.append(f"{name} with {n}: no F-ARGS")
            elif text not in hits[0].message and hits[0].data.get("allowed") != text:
                wrong.append(f"{name} with {n}: the message does not say {text!r}: {hits[0].message}")
    assert not wrong, wrong


def test_build_rules_table_names_f_args() -> None:
    rules = _plain(_section(_read(REFS / "model-spec-format.md"), "## The rules every compiled formula must pass"))
    _all(rules, ["F-ARGS", "Argument counts"], "model-spec-format.md, the rules table")
    _all(_step(4), ["F-ARGS", "exit 2", "JSON path", "Argument counts"], "SKILL.md P4")


# ----------------------------------------------------------------------------- T-4 [A-aera-6] X-OPEN

def test_x_open_fix_names_f_parse_and_f_args_in_skill_md() -> None:
    _all(_step(7), ["X-OPEN"] + T4, "SKILL.md P7")


def test_x_open_fix_names_f_parse_and_f_args_in_checks_md() -> None:
    text = _ref("checks.md")
    section = text[text.index("## What rotm.py check does"):text.index("## Common mistakes")]
    _all(section, ["X-OPEN"] + T4, "checks.md, What rotm.py check does")


@needs_plugin
def test_x_open_fix_names_f_parse_and_f_args_in_the_check_and_audit_skills() -> None:
    _all(_command_skill("check"), ["X-OPEN"] + T4, "skills/check/SKILL.md")
    _all(_command_skill("audit"), ["F-ARGS", "F-PARSE", "too few or too many arguments", "Argument counts"],
         "skills/audit/SKILL.md")


def test_x_open_fix_in_the_code() -> None:
    """The X-OPEN fix the documents quote names F-ARGS (package P1, excel.x_open_finding)."""
    assert "F-ARGS" in _read(SCRIPTS / "lib" / "excel.py"), "excel.py: the X-OPEN fix does not name F-ARGS yet"


# ----------------------------------------------------------------------------- T-5 [C-sol-3] H-BEYOND in audit

@needs_plugin
def test_audit_skill_states_h_beyond_and_the_boundary() -> None:
    _all(_command_skill("audit"), T5 + ["clear the contents", "do not delete the rows", "extend",
                                        "empty in every helper column"], "skills/audit/SKILL.md")


def test_checks_md_names_h_beyond_for_check_and_audit() -> None:
    text = _ref("checks.md")
    intro = text[:text.index("## Evidence labels")]
    _all(intro, ["when check or audit reports H-BEYOND", "X-QUERY-CHANGED", "I-HIDDEN-DECIMALS"],
         "checks.md, when to read it")
    _all(text, T5, "checks.md")


def test_audit_rule_ids_in_the_code() -> None:
    """The audit rule ids the documents name are audit rules (package P2)."""
    from lib import audit
    missing = [r for r in ("F-ARGS", "H-BEYOND") if r not in audit.RULES]
    assert not missing, f"audit.RULES has no {missing} yet"


# ----------------------------------------------------------------------------- T-10, T-11 [C-sol-6, J-1]

def test_skill_md_p3_states_how_an_empty_date_is_shown() -> None:
    _all(_step(3), T10, "SKILL.md P3")


def test_hidden_decimals_note_in_skill_md_and_the_check_skill() -> None:
    _all(_step(8), T11, "SKILL.md After a refresh")
    if HAS_PLUGIN:
        _all(_command_skill("check"), T11, "skills/check/SKILL.md")


def test_check_rule_ids_in_the_code() -> None:
    """The check rule ids the documents name are check rules (package P3)."""
    from lib import check
    missing = [r for r in ("X-QUERY-CHANGED", "I-HIDDEN-DECIMALS") if r not in check.RULES]
    assert not missing, f"check.RULES has no {missing} yet"


# ----------------------------------------------------------------------------- T-13 [A-aera-2] the query read back

def test_power_query_reference_states_the_query_read_back() -> None:
    _all(_ref("excel-power-query.md"), T13, "excel-power-query.md")


def test_query_read_back_in_skill_md_and_the_check_skill() -> None:
    _all(_step(8), T13_SHORT, "SKILL.md After a refresh")
    if HAS_PLUGIN:
        _all(_command_skill("check"), T13_SHORT, "skills/check/SKILL.md")


# ----------------------------------------------------------------------------- T-14 [A-aera-1, C-sol-1] refusal words

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_hooks_tables_list_the_refusal_and_stop_words(path: Path) -> None:
    row = _hooks_row(path, "contract gate", "raw-sheet guard")
    _all(row, T14 + T14_NO_LIST, f"{path.name} hooks table, contract gate", lower=True)


def test_p1_step_6_lists_the_refusal_and_stop_words() -> None:
    step = _substep(_step(1), 6)
    _all(step, T14 + T14_NO_LIST + ["no agreement was given; wait for the user's reply",
                                    "No need to wait, go ahead."], "SKILL.md P1 step 6", lower=True)


@needs_plugin
def test_contract_skill_step_7_lists_the_refusal_and_stop_words() -> None:
    step = _substep(_command_skill("contract"), 7)
    _all(step, T14 + T14_NO_LIST + ["no agreement was given; wait for the user's reply",
                                    "No need to wait, go ahead."], "skills/contract/SKILL.md step 7", lower=True)


def test_column_contract_lists_the_refusal_and_stop_words() -> None:
    text = _ref("column-contract.md")
    _all(text, T14 + T14_NO_LIST, "column-contract.md", lower=True)


# ----------------------------------------------------------------------------- T-15 [A-aera-5] diff and the type rules

@needs_plugin
def test_diff_skill_lists_a_changed_stored_type() -> None:
    _all(_command_skill("diff"), [T15], "skills/diff/SKILL.md")


def test_type_rules_name_a_logical_value_in_a_text_column() -> None:
    """[A-aera-5] part (1): D-TYPE (check) and R-TYPE (audit) name a cell of a text column stored as a logical
    value; the documents that describe the two rules say so."""
    words = ["text column", "logical value", "TRUE"]
    _all(_step(8), words + ["D-TYPE"], "SKILL.md step 8")
    checks = _ref("checks.md")
    _all(checks[checks.index("## What rotm.py check does"):], words + ["D-TYPE", "R-TYPE"], "checks.md")
    if HAS_PLUGIN:
        _all(_command_skill("check"), words + ["D-TYPE"], "skills/check/SKILL.md")
        _all(_command_skill("audit"), words + ["R-TYPE"], "skills/audit/SKILL.md")


def test_type_rules_in_the_code() -> None:
    """The case the documents state is in check.py (D-TYPE, package P3) and audit.py (R-TYPE, package P2)."""
    assert "stored as logical values" in _read(SCRIPTS / "lib" / "audit.py"), "audit.py: no R-TYPE logical case yet"
    assert "LOGICAL_TAG" in _read(SCRIPTS / "lib" / "check.py"), "check.py: no D-TYPE logical case yet"


# ----------------------------------------------------------------------------- [J-3] what is and is not verified

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_runs_on_the_final_v6_files_holds_the_placeholder_or_runs(path: Path) -> None:
    section = _verified(path)
    assert RUNS_V6 in section, f"{path.name}: no subsection '{RUNS_V6}'"
    for old in ("v3", "v4", "v5"):
        assert f"### Runs on the final {old} files" not in section, f"{path.name}: the {old} subsection is left"
    assert VERSION_V6 in section, f"{path.name}: the version line does not say '{VERSION_V6}'"
    bullets = _run_bullets(section)
    assert bullets, f"{path.name}: the subsection '{RUNS_V6}' holds no bullet"
    if bullets == [P_RUNS_V6]:
        return                                              # before the integrator's runs: only the placeholder
    assert P_RUNS_V6 not in " ".join(bullets), f"{path.name}: the placeholder is left beside the runs"
    for bullet in bullets:
        if bullet.startswith("Not run"):
            continue
        assert re.search(r"20\d\d-\d\d-\d\d", bullet) and re.search(r"\b\d\d:\d\d\b", bullet), \
            f"{path.name}: a run without its date and time: {bullet[:120]}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_runs_on_the_final_v6_files_name_the_required_runs(path: Path) -> None:
    """Verdict [J-3]: the whole suite without Excel in three temporary-folder set-ups, the Excel tests of the changed
    modules, selftest --quick, the selftest steps f02 and f27 alone, and whether the full strict selftest ran."""
    section = _verified(path)
    bullets = _run_bullets(section)
    if bullets == [P_RUNS_V6]:
        pytest.skip("the integrator has not written the runs yet")
    runs = _flat(" ".join(bullets))
    _all(runs, REQUIRED_RUNS, f"{path.name}, runs on the final v6 files")
    assert runs.count("--basetemp") >= 2, f"{path.name}: the two --basetemp runs are not both stated"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_no_v5_run_statement_is_carried_over(path: Path) -> None:
    text = _flat(_verified(path))
    found = [t for t in V5_RUN_TEXTS if t in text]
    assert not found, f"{path.name}: v5 run statements left: {found}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_verified_list_names_the_v6_items_with_their_tests(path: Path) -> None:
    section = _verified(path)
    verified = _flat(_section(section, "### Verified, with the tests that show it"))
    not_verified = _flat(_section(section, "### Not verified"))
    present = [item for item in V6_VERIFIED if item in verified]
    for item in present:
        missing = [t for t in V6_VERIFIED[item] if t not in verified]
        assert not missing, f"{path.name}: the verified item '{item}' does not name {missing}"
    if _run_bullets(section) == [P_RUNS_V6]:   # before the integrator's runs: every item, each with its sentence
        assert present == list(V6_VERIFIED), f"{path.name}: v6 items missing: {set(V6_VERIFIED) - set(present)}"
        assert verified.count(KEEP_V6) == len(V6_VERIFIED), f"{path.name}: {verified.count(KEEP_V6)} sentences"
    else:                                       # after them: the items kept, and no sentence for the integrator
        assert KEEP_V6 not in verified
    assert "Google" not in verified, f"{path.name}: a Google Sheets statement in the verified list"
    _all(not_verified, ["Google Sheets", "local stand-in", "not in Google Sheets",
                        "test_v6_audit.py::test_shorter_extract_repair_passes_check_and_audit"],
         f"{path.name}, Not verified")

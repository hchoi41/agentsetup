"""Tests of the v3 documents (package P7 of the v3 round, 2026-11-25). Owner: P7.

Each test reads the place where the v3 plan (section 6.7) puts a rule, and checks that the rule id or its sentence
is there, and that the texts the round removes are gone:

- [C-sol-16]: README Install and Check the installation (Python 3.11, openpyxl, pytest; a first check without Excel
  or pytest; `rotm.py selftest` without pytest prints one line and exits 3).
- [J-7]: SKILL.md and README.md "What is and is not verified": no v2 run statement; the items of the verified list
  name their tests. v4 (J-3): the subsection "Runs on the final v3 files" became "Runs on the final v4 files";
  v5 and v6 (J-3): it became "Runs on the final v5 files", then "Runs on the final v6 files"; v7 (J-6): "Runs on
  the final v7 files"; v8 (J-2): "Runs on the final v8 files"; v9 (J-2): "Runs on the final v9 files".
- v4 (A-aera-7): the first check's template list is the list of templates present (the skill copied alone lists
  its own starter only).
- The documents of the other entries of the round (contract, landing, raw layer, checks, build, hooks, templates).

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v3_docs.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PY = [sys.executable, "-P", "-X", "utf8", "-B"]

# The bullet the integrator replaces with the runs made on the final v3 files (plan section 5.4, P-RUNS).
P_RUNS = ("Not yet stated: the integrator writes here each run made on the final v3 files (command, result, test "
          "count, date and time).")

# The tests that the verified list names for the items of J-7 (plan section 5.6).
VERIFIED_TESTS = {
    "V-NO-VALUE for a volatile formula": ["test_v3_core.py::test_volatile_empty_text_is_not_v_no_value"],
    "lift after an accepted refresh": ["test_v3_lift.py::test_lift_ties_out_after_an_accepted_refresh_with_a_new_control"],
    "the empty table": ["test_v3_raw.py::test_header_only_csv_builds_audits_and_checks",
                        "test_v3_raw.py::test_empty_table_through_power_query_one_zero_one"],
    "the hand-attached query": ["test_v3_raw.py::test_hand_attached_query_is_accepted"],
    "the acceptance rules": ["test_v3_raw.py::test_accept_refresh_after_land_force_keeps_the_csv",
                             "::test_accept_refresh_without_excel_accepts_nothing",
                             "::test_inputs_sheet_value_is_not_a_control",
                             "::test_contract_agreed_again_fails_check_and_audit_until_rebuild"],
}

# Numbers of the v2 selftest run and of the v2 layout runs that v3 must not carry (J-7).
V2_RUN_TEXTS = ("2,081", "2,074", "52 minutes", "11:28 to 12:20", "278 files", "48 PNG", "76 Excel jobs", "20,581",
                "for version v2", "rebuilt with the v2 code", "55 tests")


# ----------------------------------------------------------------------------- helpers

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _section(text: str, heading: str) -> str:
    """The text from `heading` to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading)
    rest = text[start + len(heading):]
    m = re.search(r"^#{1,%d} " % level, rest, flags=re.M)
    return heading + (rest[:m.start()] if m else rest)


def _skill_md() -> str:
    return _read(SKILL / "SKILL.md")


def _step(number: int) -> str:
    """Procedure step `number` of SKILL.md, flattened."""
    proc = _section(_skill_md(), "## Procedure")
    m = re.search(rf"^{number}\. \*\*", proc, flags=re.M)
    assert m, f"SKILL.md: no procedure step {number}"
    rest = proc[m.start():]
    n = re.search(rf"^{number + 1}\. \*\*", rest, flags=re.M)
    return _flat(rest[:n.start()] if n else rest)


def _substep(step: str, number: int) -> str:
    """Sub-step `number` (`   <n>. `) of a flattened procedure step."""
    m = re.search(rf"(?:^| ){number}\. ", step)
    assert m, f"no sub-step {number}"
    rest = step[m.end():]
    n = re.search(rf" {number + 1}\. ", rest)
    return rest[:n.start()] if n else rest


def _ref(name: str) -> str:
    return _flat(_read(REFS / name))


def _command_skill(name: str) -> str:
    return _flat(_read(PLUGIN / "skills" / name / "SKILL.md"))


def _all(text: str, needed: list[str] | tuple[str, ...], where: str) -> None:
    missing = [n for n in needed if n not in text]
    assert not missing, f"{where}: missing {missing}"


def _template_readmes() -> list[Path]:
    files = [SKILL / "templates" / "starter" / "README.md"]
    if HAS_PLUGIN:
        files += [PLUGIN / "templates" / "README.md"]
        files += sorted((PLUGIN / "templates").glob("*/README.md"))
    return files


def _documents() -> list[Path]:
    files = [SKILL / "SKILL.md"] + sorted(REFS.glob("*.md")) + _template_readmes()
    if HAS_PLUGIN:
        files += [PLUGIN / "README.md"] + sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    return files


# ----------------------------------------------------------------------------- [C-sol-16] README Install

@needs_plugin
def test_readme_install_names_python_3_11_openpyxl_and_pytest() -> None:
    install = _flat(_section(_read(PLUGIN / "README.md"), "## Install"))
    _all(install, ["Python 3.11 or later", "`-P`", "openpyxl", "every command", "pytest",
                   "`rotm.py selftest` and the tests", "exits 3", "no traceback"], "README Install")
    assert "Python 3.10" not in install and "No other package is needed" not in install


@needs_plugin
def test_readme_first_check_needs_neither_excel_nor_pytest() -> None:
    install = _flat(_section(_read(PLUGIN / "README.md"), "## Install"))
    _all(install, ["needs neither Excel nor pytest", "init --list-templates", 'formula "=SUM(A1:A3)"',
                   "selftest --strict"], "README Check the installation")
    assert install.index("init --list-templates") < install.index("selftest --strict"), \
        "the first check comes before the full selftest"


_BLOCK_PYTEST = ("import sys; sys.modules['pytest'] = None; sys.path.insert(0, sys.argv[1]); import rotm; "
                 "sys.exit(rotm.main(sys.argv[2:]))")
# The same, and no child process may start: a selftest that went past the pytest test would start its steps as
# subprocesses; here the first one ends the run with exit 99 instead of running the selftest.
_BLOCK_PYTEST_AND_CHILDREN = (
    "import sys, subprocess\n"
    "def _no_child(*a, **k):\n"
    "    raise SystemExit(99)\n"
    "subprocess.run = subprocess.Popen = _no_child\n"
    "sys.modules['pytest'] = None\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import rotm\n"
    "sys.exit(rotm.main(sys.argv[2:]))\n")


def _rotm_without_pytest(*args: str, children: bool = True) -> subprocess.CompletedProcess[str]:
    """rotm.py in a Python where `import pytest` fails (sys.modules entry None) and Excel is switched off; with
    children=False no child process may start."""
    env = dict(os.environ, ROTM_NO_EXCEL="1")
    code = _BLOCK_PYTEST if children else _BLOCK_PYTEST_AND_CHILDREN
    return subprocess.run(PY + ["-c", code, str(SCRIPTS), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=300)


def _templates_present() -> list[str]:
    """The templates `init --list-templates` lists: the skill's own, and the plugin's only when the plugin is
    there (v4, A-aera-7: the skill folder copied alone passes its own tests)."""
    roots = [SKILL / "templates"] + ([PLUGIN / "templates"] if HAS_PLUGIN else [])
    return sorted({d.name for r in roots if r.is_dir() for d in r.iterdir() if (d / "model.spec.json").is_file()})


@pytest.mark.parametrize("args", [("init", "--list-templates"), ("formula", "=SUM(A1:A3)")])
def test_the_first_check_commands_run_without_excel_and_pytest(args: tuple[str, ...]) -> None:
    proc = _rotm_without_pytest(*args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    if args[0] == "init":
        expected = _templates_present()
        assert "starter" in expected
        missing = [name for name in expected if name not in proc.stdout]
        assert not missing, (missing, proc.stdout)
    else:
        assert "depth:      1 (limit 2): SUM" in proc.stdout


def test_selftest_without_pytest_prints_one_line_and_exits_3_end_to_end() -> None:
    from lib import selftest
    proc = _rotm_without_pytest("selftest", "--strict", children=False)
    lines = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
    assert proc.returncode == 3, lines
    assert lines == [selftest.N_PYTEST], lines
    assert "Traceback" not in proc.stdout + proc.stderr


# ----------------------------------------------------------------------------- [J-7] what is and is not verified

def _verified(path: Path) -> str:
    return _section(_read(path), "## What is and is not verified")


def _verified_files() -> list[Path]:
    return [SKILL / "SKILL.md"] + ([PLUGIN / "README.md"] if HAS_PLUGIN else [])


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_no_v2_run_statement_is_carried_over(path: Path) -> None:
    text = _flat(_verified(path))
    found = [t for t in V2_RUN_TEXTS if t in text]
    assert not found, f"{path.name}: v2 run statements left: {found}"
    assert not re.search(r"selftest --strict` (?:exits|exited) 0", text)


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_the_v3_runs_subsection_is_replaced_by_the_v4_one(path: Path) -> None:
    """v4 to v6, J-3; v7, J-6; v8 and v9, J-2: the runs on the final v3 to v8 files are not carried over; the
    subsection names the v9 files and holds the integrator's line or the integrator's dated runs (test_v9_docs.py
    checks its contents)."""
    section = _verified(path)
    assert "### Runs on the final v3 files" not in section
    assert "### Runs on the final v4 files" not in section
    assert "### Runs on the final v5 files" not in section
    assert "### Runs on the final v6 files" not in section
    assert "### Runs on the final v7 files" not in section
    assert "### Runs on the final v8 files" not in section
    assert "### Runs on the final v9 files" in section
    runs = _flat(_section(section, "### Runs on the final v9 files"))
    assert P_RUNS not in runs and P_RUNS.replace("v3", "v4") not in runs, f"{path.name}: an earlier placeholder is left"
    for old in ("v5", "v6", "v7"):
        assert f"Integrator: replace this line with the runs on the final {old} files." not in runs, \
            f"{path.name}: the {old} placeholder is left"
    assert "Integrator: write here the runs made on the final v8 files (plan section 8)." not in runs, \
        f"{path.name}: the v8 line is left"
    assert "Integrator: write here the runs made on the final v9 files (plan section 8)." in runs \
        or re.search(r"20\d\d-\d\d-\d\d", runs), \
        f"{path.name}: the subsection holds neither the integrator's line nor a dated run"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_the_verified_list_names_the_tests_of_each_moved_item(path: Path) -> None:
    section = _verified(path)
    verified = _flat(_section(section, "### Verified, with the tests that show it"))
    not_verified = _flat(_section(section, "### Not verified"))
    for item, tests in VERIFIED_TESTS.items():
        missing = [t for t in tests if t not in verified]
        assert not missing, f"{path.name}: the verified item '{item}' does not name {missing}"
    assert "also lists a volatile formula" not in not_verified
    assert "lift takes the control total from the contract" not in not_verified


# ----------------------------------------------------------------------------- SKILL.md, P1 ([J-4], [C-sol-3], [A-aera-3, C-sol-4])

def test_p1_step_1_short_table_name_and_contract_remove() -> None:
    step = _substep(_step(1), 1)
    _all(step, ["short table name", "12 characters", "raw_<t>", "h_<t>", "100-character",
                "ROTM contract remove MODEL --table <t>", "MODEL/removed/<t>_<YYYYMMDD_HHMMSS>/",
                'model.spec.json` has a sheet with `"table": "<t>"`'], "SKILL.md P1 step 1")


def test_p1_contract_show_after_every_edit() -> None:
    step = _step(1)
    _all(_substep(step, 2), ["After every edit of the JSON run `ROTM contract show MODEL --table <t>`"],
         "SKILL.md P1 step 2")
    _all(_substep(step, 4), ["Never show an `.md` written before the last edit of the JSON"], "SKILL.md P1 step 4")
    _all(_substep(step, 5), ["ROTM contract show MODEL --table <t>", "show the user what it writes",
                             "the contract changed after it was last shown", "exit 2, nothing recorded"],
         "SKILL.md P1 step 5")


def test_p1_step_6_change_requests_and_partial_agreements() -> None:
    step = _substep(_step(1), 6)
    _all(step, ["asks for a change", "change, rename, remove, drop, replace, switch, fix, correct, revise or modify",
                "바꿔", "no changes", "nothing to change", "agrees without change", "copied exactly",
                "sentence of the task file"], "SKILL.md P1 step 6")


# ----------------------------------------------------------------------------- SKILL.md, P2 to P8

def test_p2_hand_attached_query_pq_switch_precision_and_empty_extract() -> None:
    step = _step(2)
    _all(step, ["raw_<t>!A1", "attached by hand", "--pq-source", "--rewrite-queries --pq-source <kind>",
                "never exits 0 having kept a query of the other kind", "L-PRECISION", "15 significant digits",
                "never rounded up", "UTC offset", "--allow-empty", "zero helper rows"], "SKILL.md P2")


def test_p3_null_flag_for_known_nulls() -> None:
    _all(_step(3), ["`<column>_null`", "known_nulls"], "SKILL.md P3")
    _all(_ref("model-spec-format.md"), ["and for a column whose contract agrees `known_nulls` for it"],
         "model-spec-format.md helper table")


def test_w_self_check_in_p5_checks_md_and_build_skill() -> None:
    _all(_step(5), ["W-SELF-CHECK", "independent total"], "SKILL.md P5")
    _all(_ref("checks.md"), ["W-SELF-CHECK", "can never fail"], "checks.md")
    if HAS_PLUGIN:
        _all(_command_skill("build"), ["W-SELF-CHECK"], "skills/build/SKILL.md")


V_NO_VALUE_TEXTS = ["no value (circular reference)", "find the cycle", "named no circular reference",
                    "is not reported"]


def test_v_no_value_fixes_in_p7_checks_md_and_check_skill() -> None:
    _all(_step(7), ["no value (circular reference)", "find the cycle", "recalculate", "names a circular reference",
                    "volatile function", "is not reported"], "SKILL.md P7")
    checks = _ref("checks.md")
    intro = checks[:checks.index("## Evidence labels")]
    _all(intro, V_NO_VALUE_TEXTS + ["names a circular reference when there is one"], "checks.md, first lines")
    step7 = checks[checks.index("7. It reads back"):checks.index("8. It prints")]
    _all(step7, ["find the cycle", "recalculate the workbook", "is not reported"], "checks.md, check step 7")
    if HAS_PLUGIN:
        _all(_command_skill("check"), ["no value (circular reference)", "is not reported", "recalculate"],
             "skills/check/SKILL.md step 1")


ACCEPT_RULES = ["--control", "controls.json", "Never the inputs sheet", "D-CONTROL-INPUT", "CT-VERSION",
                "X-NO-QUERY", "D-TYPE", "D-KEY-SEPARATOR", "R-CHANGED"]


def test_after_a_refresh_in_skill_md() -> None:
    step = _step(8)
    _all(step, ACCEPT_RULES + ["R-TYPE", "find the missing or extra rows", "landed after", "built before v3",
                               "no refresh ran", "is not 0", "missing, invalid, proposed, stale",
                               "nothing was refreshed", "row 2 included", "From zero helper rows"],
         "SKILL.md step 8")
    assert "type the" not in step.lower()


def test_after_a_refresh_in_checks_md() -> None:
    text = _ref("checks.md")
    section = text[text.index("## What `rotm.py check` does"):text.index("## Common mistakes")]
    in_section = [r for r in ACCEPT_RULES if r != "D-KEY-SEPARATOR"]
    _all(section, in_section + ["R-TYPE", "workbook_digest", "find the missing or extra rows",
                                "built before v3", "nothing was refreshed", "missing, invalid, proposed, stale",
                                "query_attach", "row 2 included", "byte for byte"], "checks.md, check")
    builds = text[text.index("## Checks `build` writes for every raw table"):text.index("## Checks you add")]
    _all(builds, ["D-KEY-SEPARATOR", "The key check of `rotm.py check` stands"], "checks.md, the key checks")


def test_after_a_refresh_in_model_spec_format_landing() -> None:
    text = _ref("model-spec-format.md")
    section = text[text.index("## Landing raw data"):text.index("## What build writes")]
    _all(section, ACCEPT_RULES + ["R-TYPE", "workbook_digest", "contract_sha256", "find the missing or extra rows",
                                  "L-PRECISION"], "model-spec-format.md, Landing raw data")


def test_after_a_refresh_in_column_contract_md() -> None:
    text = _ref("column-contract.md")
    section = text[text.index("## What the plugin does with the contract later"):text.index("## Example")]
    _all(section, ["--control", "controls.json", "never a control total", "D-CONTROL-INPUT", "CT-VERSION",
                   "contract_sha256", "L-PRECISION", "15 significant digits", "never rounded up"],
         "column-contract.md, later")


@needs_plugin
def test_after_a_refresh_in_check_skill() -> None:
    _all(_command_skill("check"), ACCEPT_RULES + ["missing, invalid, proposed, stale", "zero rows",
                                                  "raw_<t>!A1", "no refresh ran"], "skills/check/SKILL.md")


# ----------------------------------------------------------------------------- [A-aera-2] inputs, [C-sol-14] hooks

INPUTS_RULE = ["Inputs are changed in `model.spec.json`", "not kept by a rebuild", "`--force`"]


def test_common_mistakes_say_inputs_are_changed_in_the_specification() -> None:
    _all(_flat(_section(_skill_md(), "## Common mistakes")), INPUTS_RULE + ["also after a `check`"],
         "SKILL.md Common mistakes")


@pytest.mark.parametrize("path", _template_readmes(), ids=lambda p: p.parent.name)
def test_every_template_readme_says_inputs_are_changed_in_the_specification(path: Path) -> None:
    _all(_flat(_read(path)), INPUTS_RULE, f"{path.parent.name}/README.md")


@needs_plugin
def test_rolling_forecast_readme_no_longer_changes_inputs_on_the_sheet() -> None:
    text = _flat(_read(PLUGIN / "templates" / "rolling_forecast" / "README.md"))
    assert "forecast months on the inputs sheet, rebuild" not in text
    assert "in `model.spec.json` (the items of the inputs sheet)" in text


@pytest.mark.parametrize("path", [SKILL / "SKILL.md"] + ([PLUGIN / "README.md"] if HAS_PLUGIN else []),
                         ids=lambda p: p.name)
def test_hooks_tables_let_a_copy_to_a_new_workbook_through(path: Path) -> None:
    hooks = _flat(_section(_read(path), "## Hooks" + (" (when the plugin is installed)" if path.name == "SKILL.md"
                                                       else "")))
    row = hooks.split("| raw-sheet guard |", 1)[1].split("| audit on write |", 1)[0]
    _all(row, ["new workbook name", "Copy-Item", "shutil"], f"{path.name} hooks table, raw-sheet guard")
    _all(hooks, ["asks for a change"], f"{path.name} hooks table, contract gate")


# ----------------------------------------------------------------------------- skills/build/SKILL.md

@needs_plugin
def test_build_skill_states_ownership_zero_rows_header_formats_and_manifest_fields() -> None:
    _all(_command_skill("build"), ["chain of writes from its own output", "writes.json", "checked (or audited)",
                                   "zero helper rows", "ends at row 2", "input list", "ISO date",
                                   "A text label keeps General", "contract_sha256", "workbook_digest",
                                   "CT-VERSION"], "skills/build/SKILL.md")


def test_model_spec_format_states_the_ownership_chain() -> None:
    text = _ref("model-spec-format.md")
    section = text[text.index("## What build writes"):text.index("## Styles")]
    _all(section, ["chain of writes from its own output", "after a `check`", "not kept by a rebuild",
                   "contract_sha256", "workbook_digest"], "model-spec-format.md, What build writes")
    assert "byte for byte a file a writing command" not in section
    capacity = text[text.index("### Capacity, growth and shrink"):text.index("## Calculation blocks")]
    _all(capacity, ["zero helper rows", "`capacity` 0", "`last_row` 1", "row 2 included"], "model-spec-format.md, Capacity")
    assert "(at least 1 row)" not in capacity


# ----------------------------------------------------------------------------- column-contract.md

def test_column_contract_md_contract_rules_of_v3() -> None:
    text = _ref("column-contract.md")
    _all(text, ["at most 12 advised", "`shown_sha256`", "contract show", "Never show an `.md` written before the last "
                "edit", "the contract changed after it was last shown", "asks for a change", "바꿔",
                "nothing to change", "agrees without change", "copied exactly", "### When a table is not wanted",
                'rotm.py contract remove "<model>" --table <t>', "removed/<t>_<YYYYMMDD_HHMMSS>/"],
         "column-contract.md")


# ----------------------------------------------------------------------------- quick reference ([A-aera-12] part 3)

def test_quick_reference_says_the_example_is_static_and_names_its_power_query_variant() -> None:
    qr = _flat(_section(_skill_md(), "## Quick reference"))
    _all(qr, ["static snapshots", "stand in for query results", "builds without Excel", "Power Query variant",
              "examples/sales_vs_target/task.md"], "SKILL.md Quick reference")


# ----------------------------------------------------------------------------- removed texts

REMOVED = ("type the new total", "type the source system's new total", "Python 3.10", "No other package is needed",
           "If the three agree")


def test_removed_texts_are_gone_from_the_documents() -> None:
    found = [f"{p.relative_to(PLUGIN if HAS_PLUGIN else SKILL).as_posix()}: {t}" for p in _documents()
             for t in REMOVED if t in _flat(_read(p))]
    assert not found, found


def test_the_total_check_fix_no_longer_says_to_type_the_total() -> None:
    """F-TOTAL (plan 5.4; check.py, package P2): the fix of a failing <t>_total_<column> check no longer says to type
    the total into the control cell."""
    source = _read(SCRIPTS / "lib" / "check.py")
    assert "type the source system's new total" not in source

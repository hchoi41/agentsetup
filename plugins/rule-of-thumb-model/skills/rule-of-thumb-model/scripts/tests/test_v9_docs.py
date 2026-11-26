"""Tests of the v9 documents (package P8 of the v9 round, 2026-11-26). Owner: P8.

Each test reads the place where the v9 plan (sections 5.2 and 5.4) puts a text and checks that the text is there, in
words that do not depend on line breaks (whitespace is made uniform and code-span backticks are removed):

- D-1 ([A-aera-2, C-sol-1], the order of control totals): ORDER and ORDER-STALE in SKILL.md (the P2 bullet "A new
  extract whose source total changed" ends with ORDER-STALE; step 8.4), column-contract.md, checks.md (both also
  with ORDER-DETAIL), model-spec-format.md, skills/check/SKILL.md and skills/lift/SKILL.md; the v8 order texts are
  gone from every file P8 owns.
- D-2 ([A-aera-4]): --control only for a column whose control total the agreed contract names.
- D-3 ([A-aera-7]): key uniqueness on the values raw/<t>.csv stores (the L-PRECISION bullet of SKILL.md P2).
- D-4 ([A-aera-1, C-sol-2]): the self-mismatch rule with its third case (SKILL.md P5; checks.md, the bracket of the
  second case and item 9).
- D-5 ([A-aera-11]): C-BLOCK through a total or link cell (SKILL.md quick reference; checks.md, the C-BLOCK
  paragraph).
- D-6 ([C-sol-3] full form; or R-6 under "Not verified" when the integrator swapped it in): the queries a query
  attached by hand reads are compared at a refresh.
- D-7 ([C-sol-7]): the refusal of a query read whose file would collide with another query's file.
- D-8 ([C-sol-5]): the stale pass in the hooks tables of SKILL.md and README.md.
- D-9 ([A-aera-5]): diff's two fields, in the quick reference of SKILL.md and the commands table of README.md.
- D-10 ([A-aera-9, A-aera-12]): added only where a file states what compat reads outside cells (neither does).
- D-11 and [J-1]: SKILL.md step 0 and the quick reference say where the template folders are; init --list-templates
  prints an existing folder for each template, in the plugin and in the skill folder copied alone.
- D-12: the statements P8 changed because an entry made them untrue (lift after an accepted refresh, a later build
  of a landed export, a rebuild that keeps an accepted total).
- [J-2]: SKILL.md and README.md "What is and is not verified": the version line, the head bullets, the subsection
  "Runs on the final v9 files" (the integrator's line, or the integrator's dated runs that name the required runs),
  no v8 run statement, the four v9 items with their tests (each test exists) and the integrator's sentence (before
  the runs every v9 item has it, after them none). test_v2_docs.py compares the runs of the two files;
  test_v8_docs.py keeps every Google Sheets statement under "Not verified".

The v9 items name tests of P2 (test_v9_controls.py, test_v9_land.py), P3 (test_v9_criteria.py) and P6
(test_v9_hooks.py): test_v9_verified_items_name_their_tests fails until those files exist (plan section 7, I-1).

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v9_docs.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PY = [sys.executable, "-P", "-X", "utf8", "-B"]

# ----------------------------------------------------------------------------- the texts of plan sections 5.2, 5.4

ORDER = ("The control total used for a table and column is, in this order: --control given in this run; else the total "
         "accepted for this snapshot in raw/<t>.controls.json under the agreement in force; else the agreed contract's "
         "total.")
ORDER_STALE = ("A total accepted under an earlier agreement (the contract was agreed again since, for example with "
               "another total) is not used: land, build, check and lift print a note that names it, and when the rows "
               "do not meet the contract's total, L-TOTAL (land) or D-TOTAL (check) names both totals. Give --control "
               "with the source's current total; it is recorded under the agreement in force.")
ORDER_DETAIL = ("Each entry of raw/<t>.controls.json records the hash of the agreed contract it was accepted under "
                "(contract_sha256). An entry written before v9 has no hash; it is used only when its time is not "
                "earlier than the time of the current agreement, and not when either time is missing. Recording the "
                "same values again under the same agreement keeps the entry as it is; a record under another agreement "
                "replaces it. contract show and contract agree with unchanged content keep the entry in force.")
# The v8 statements of the order that v9 makes untrue (an accepted total used whatever the agreement).
V8_ORDER_TEXTS = ("the total recorded for this exact snapshot", "the total accepted for this exact snapshot",
                  "take the place of the contract's", "Without --control, land compares the file with the control "
                  "total in force.", "take the control total of the snapshot they read from that file",
                  "Later builds and checks of the same snapshot use that total.", "a rebuild then keeps it; the "
                  "contract is not changed", "The control total a check uses for a table is:",
                  "The control total check uses for a table and column, in this order")
D2 = ("--control is accepted only for a column whose control total the agreed contract names: a control total is part "
      "of the agreement. For any other column land and check --accept-refresh refuse (exit 2; nothing is written or "
      "recorded). Add the total to the contract (contract propose ... --force ... --total <column>=<source total>), "
      "show it and get it agreed; build then writes its check <t>_total_<column>.")
D3 = ("Key uniqueness is decided on the key values as raw/<t>.csv stores them: two number keys that differ only after "
      "the 15th significant digit are one key in the snapshot, and land refuses them with L-KEY, naming both lines "
      "and both values; nothing is written. Make such a key column type text in the contract.")
D4_BRACKET = ("(for example the texts TRUE and FALSE in any case, which Excel reads as logical values; a label from a "
              "cell that starts with =, < or >, which Excel reads as a comparison; a label that holds ~* or ~?, which "
              "Excel reads as a literal * or ?)")
D4_FIX = ("For TRUE, FALSE and a label that starts with an operator, use a helper key with a fixed prefix on both "
          "sides; for a label that holds ~, * or ?, use helper keys that replace these signs by fixed texts on both "
          "sides")
D4 = ("A label that Excel's criteria matching does not match with itself " + D4_BRACKET + " leaves the rows with that "
      "label out of the sum. Build and check warn D-CRITERIA with the reason self_mismatch and name the label and the "
      "first raw row. " + D4_FIX + " (references/helper-patterns.md, \"A label that Excel's criteria matching does "
      "not match with itself\").")
D4_ITEM9 = ("(for example the texts TRUE and FALSE in any case, a label from a cell that starts with =, < or >, or a "
            "label that holds ~* or ~?; reason self_mismatch)")
D5 = ("(a check that reads the block's total cell, or a link to it, counts as reading the block; at most two such "
      "cells are followed)")
D6 = ("With Excel, check --refresh and check --accept-refresh also compare each query that a query attached by hand "
      "reads (the connection-only queries build carried over, recorded in the manifest as query_reads) with the hash "
      "build recorded: a changed, missing or unreadable one is X-QUERY-CHANGED and names that query; the table is not "
      "refreshed, nothing is accepted, and raw/<t>.csv, raw/<t>.controls.json and the manifest stay byte for byte.")
R6 = ("A change of a query that a query attached by hand reads (a staging query) is not found at a refresh: only the "
      "query that loads the raw table is compared (v9 reduced [C-sol-3, C-sol-7] part (2)).")
D7 = ("A query read whose file queries/<name>.hand.pq.m would be the file of a raw table's query attached by hand or "
      "of another query read (the names are equal without regard to case) is refused (exit 2, nothing written), and "
      "the refusal names both queries; rename one of them in Excel, or pass --drop-hand-queries.")
T3_FULL_END = ("A query it reads that cannot be made again (for example one that reads an Excel table of the workbook) "
               "is named in the refusal (exit 2, nothing written).")
D8 = ("A passing audit or check counts only while raw/ and the agreed contracts of the model folder are as they were "
      "when it ran (each audit, check and recalc record in reports/status.json holds their fingerprint, inputs): after "
      "a landing or a new agreement the stop is refused once, and the refusal names the table and the command that "
      "brings the workbook up to date (build, or check --refresh for a table loaded by a query).")
T8_V8 = ("a later stop in the same session does not refuse again for a workbook unchanged since that message; a "
         "workbook written after it is judged again.")
# D-9 with the wording of request v9_P1 R1 (diff gives the recalc advice only while a file's version has no
# recalculation on record; a workbook stays set to manual calculation after rotm.py recalc).
D9 = ("diff reports saved_values_compared (both files hold values saved by Excel) and recalculated (true only when "
      "reports/status.json of each file's model folder records a passing check or recalc of that file's current "
      "version); for a workbook set to manual calculation it prints a note that names the file; while that file's "
      "version has no passing check or recalc on record, the note says that its saved values may be out of date and "
      "to run rotm.py recalc on both files before the diff.")
D10 = ("compat applies its argument rules (for example CONCAT with more than two arguments or a range, FILTER with a "
       "third argument) also to the formulas of data validations, conditional-format rules and defined names; they "
       "are reported, not rewritten, and --strict exits non-zero.")
D11 = ("ROTM init --list-templates prints each template's name and its folder. starter lies in the skill folder "
       "(<SKILL_DIR>/templates/starter); ar_aging, budget_vs_actual, price_volume_mix and rolling_forecast lie in the "
       "plugin's templates/ folder and are listed only when the whole plugin is installed. Read a template's files "
       "(its README, contracts and specification) in MODEL after init has copied them there.")
PLUGIN_TEMPLATES = ("ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast")
# D-12: the statements P8 changed (each as it reads now).
D12_SKILL = ["lift takes the control total accepted for the snapshot it reads under the agreement in force from "
             "raw/<t>.controls.json and lift --run ties out.",
             "a later build --strict under the same agreement takes the recorded total.",
             "Later builds and checks of the same snapshot use that total while the contract is not agreed again."]
D12_README = ["(the control total accepted for the snapshot under the agreement in force, from raw/<t>.controls.json; "
              "lift --run ties out)",
              "a later build under the same agreement takes the recorded total"]

# ----------------------------------------------------------------------------- [J-2] (plan sections 5.3 and 5.5)

RUNS_V9 = "### Runs on the final v9 files"
P_RUNS_V9 = "Integrator: write here the runs made on the final v9 files (plan section 8)."
KEEP = "Integrator: keep this item only when these tests passed in the final runs."
VERSION_V9 = re.compile(r"Version v9 of 20\d\d-\d\d-\d\d\.")
HEAD_V9 = ["The runs made on the final v9 files are listed in the first subsection.",
           "An item of the verified list holds for v9 only when the tests it names passed in one of those runs."]
V9_VERIFIED = {
    "An accepted control total bound to its agreement (v9)": [
        "test_v9_controls.py::test_land_control_record_is_not_used_after_a_new_agreement",
        "test_v9_controls.py::test_acceptance_record_is_not_used_after_a_new_agreement",
        "test_v9_controls.py::test_build_and_check_use_the_new_contract_total_after_a_new_agreement",
        "test_v9_controls.py::test_land_force_control_after_a_new_agreement_records_the_new_hash",
        "test_v9_controls.py::test_entry_under_the_agreement_in_force_still_applies",
        "test_v9_controls.py::test_entry_without_hash_applies_only_when_not_earlier_than_the_agreement",
        "test_v9_controls.py::test_show_and_agree_with_unchanged_content_keep_the_entry"],
    "The self-mismatch finding for labels that hold ~* or ~? (v9)": [
        "test_v9_criteria.py::test_tilde_wildcard_labels_netting_to_zero_fail_build_and_check",
        "test_v9_criteria.py::test_helper_keys_for_tilde_labels_pass_and_equal_python",
        "test_v9_criteria.py::test_tilde_without_wildcard_ordinary_labels_and_shipped_models_give_no_self_mismatch"],
    "The refusal of --control for a column without a contract total (v9)": [
        "test_v9_land.py::test_control_without_a_contract_total_is_refused_by_land",
        "test_v9_land.py::test_control_without_a_contract_total_is_refused_by_accept_refresh",
        "test_v9_land.py::test_contracted_total_lands_and_build_writes_its_check"],
    "A pass made stale by a later landing in the finish check (v9)": [
        "test_v9_hooks.py::test_landing_after_the_pass_is_refused_once_naming_the_table_and_the_rebuild",
        "test_v9_hooks.py::test_contract_agreed_again_after_the_pass_makes_it_stale",
        "test_v9_hooks.py::test_session_without_a_landing_after_the_pass_behaves_as_in_v8"],
}
# Numbers and words of the v8 runs that v9 must not carry ([J-2]: do not carry over the v8 numbers).
V8_RUN_TEXTS = ("3,176 passed", "2,694 passed", "2026-11-26, 15:13 to 15:27", "2026-11-26, 16:15 to 16:27",
                "2026-11-26, 14:59 to 16:14", "2026-11-26, 15:13 to 15:22", "2026-11-26, 14:56 to 14:59",
                "14:56:19 to 14:56:25", "2026-11-26, 14:51 to 14:52", "the 53 test files", "Version v8",
                "on the final v8 files", "for v8 only", "Runs on the final v8 files",
                "Integrator: write here the runs made on the final v8 files (plan section 8).")
# The runs the integrator states (verdict [J-2]; plan section 8, step 7): the whole suite without Excel with the
# default temporary folder, with --basetemp inside the skill folder and with a --basetemp of about 150 characters
# (each removed before the next run), the Excel tests (-m excel), selftest --quick and the two steps run alone.
REQUIRED_RUNS = ("ROTM_NO_EXCEL=1", "scripts/tests", "default temporary folder", "inside the skill folder",
                 "150 characters", "removed", "-m excel", "selftest --quick", "f02_skill_alone", "f27_power_query",
                 "alone", "selftest --strict")


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


def _bullet_of(text: str, start: str) -> str:
    """The bullet (or sub-bullet) of flattened `text` that starts with `start`, up to the next bullet mark (a dash, or
    a number with a full stop before a capital letter: "add up to 0. build" is no mark)."""
    i = text.index(start)
    m = re.search(r" - | \d+\. (?=[A-Z])", text[i + len(start):])
    return text[i:i + len(start) + m.start()] if m else text[i:]


def _quick_row(want: str) -> str:
    rows = [ln for ln in _section(_skill_md(), "## Quick reference").splitlines() if ln.startswith(f"| {want} |")]
    assert len(rows) == 1, f"SKILL.md quick reference: {len(rows)} rows for '{want}'"
    return _plain(rows[0])


def _hooks_row(path: Path, hook: str) -> str:
    heading = "## Hooks" + (" (when the plugin is installed)" if path.name == "SKILL.md" else "")
    lines = [ln for ln in _section(_read(path), heading).splitlines() if ln.startswith(f"| {hook} |")]
    assert len(lines) == 1, f"{path.name}: {len(lines)} rows for the hook '{hook}'"
    return _plain(lines[0])


def _verified_files() -> list[Path]:
    return [SKILL / "SKILL.md"] + ([PLUGIN / "README.md"] if HAS_PLUGIN else [])


def _p8_files() -> list[Path]:
    files = [SKILL / "SKILL.md", REFS / "column-contract.md", REFS / "checks.md", REFS / "model-spec-format.md"]
    if HAS_PLUGIN:
        files += [PLUGIN / "README.md", PLUGIN / "skills" / "check" / "SKILL.md",
                  PLUGIN / "skills" / "lift" / "SKILL.md", PLUGIN / "skills" / "contract" / "SKILL.md"]
    return files


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


def _run_bullets(section: str) -> list[str]:
    return _bullets(_section(section, RUNS_V9)[len(RUNS_V9):])


def _before_the_runs(section: str) -> bool:
    """True while the subsection holds only the integrator's line (the runs on the final v9 files not yet written)."""
    return _run_bullets(section) == [P_RUNS_V9]


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


def _list_templates(rotm: Path, cwd: Path) -> list[tuple[str, Path]]:
    proc = subprocess.run(PY + [str(rotm), "init", "--list-templates"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(cwd), timeout=120)
    assert proc.returncode == 0, (proc.stdout[-2000:], proc.stderr[-2000:])
    out: list[tuple[str, Path]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        assert "  " in line, f"init --list-templates: a line without a folder: {line!r}"
        name, folder = line.split("  ", 1)
        out.append((name.strip(), Path(folder.strip())))
    return out


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder)
    except OSError:
        return False


# ----------------------------------------------------------------------------- D-1 the order of control totals

def test_d1_skill_md_p2_bullet_ends_with_order_stale_and_the_control_rule() -> None:
    """D-1 and D-2: the P2 bullet "A new extract whose source total changed" ends with ORDER-STALE in place of the v8
    sentence, then D-2."""
    step = _step(2)
    bullet = _bullet_of(step, "A new extract whose source total changed")
    _before(bullet, "A given total that does not equal the file's sum is L-TOTAL, and nothing is written.",
            ORDER_STALE, "SKILL.md P2, the bullet of a new extract")
    _before(bullet, ORDER_STALE, D2, "SKILL.md P2, the bullet of a new extract")
    assert bullet.rstrip().endswith(D2), f"SKILL.md P2: D-2 does not end the bullet: {bullet[-200:]}"


def test_d1_skill_md_step_8_states_the_order_and_the_stale_total() -> None:
    step = _step(8)
    bullet = _bullet_of(step, ORDER)
    _before(bullet, ORDER, ORDER_STALE, "SKILL.md step 8.4, the order of control totals")
    _before(bullet, ORDER_STALE, "Never the inputs sheet", "SKILL.md step 8.4, the order of control totals")
    _all(step, ["D-CONTROL-INPUT"], "SKILL.md step 8.4")


@pytest.mark.parametrize("name", ["column-contract.md", "checks.md"])
def test_d1_column_contract_and_checks_state_the_order_with_its_detail(name: str) -> None:
    text = _plain(_read(REFS / name))
    _before(text, ORDER, ORDER_STALE, name)
    _before(text, ORDER_STALE, ORDER_DETAIL, name)
    i = text.index(ORDER)
    assert text.index(ORDER_DETAIL, i) - i < len(ORDER) + len(ORDER_STALE) + 10, \
        f"{name}: ORDER, ORDER-STALE and ORDER-DETAIL are not one after the other"
    _all(text, ["Never the inputs sheet" if name == "checks.md" else "never a control total", "D-CONTROL-INPUT"],
         name)


def test_d1_model_spec_format_states_the_order_in_landing_and_in_what_build_writes() -> None:
    text = _read(REFS / "model-spec-format.md")
    landing = _plain(_section(text, "## Landing raw data"))
    assert landing.count(ORDER) == 2, f"model-spec-format.md, Landing raw data: ORDER {landing.count(ORDER)} times"
    assert landing.count(ORDER_STALE) == 2, "model-spec-format.md, Landing raw data: ORDER-STALE not twice"
    _before(landing, "the landed values are added as decimals; which control total is used: the next bullet",
            ORDER, "model-spec-format.md, Landing raw data")
    build = _plain(_section(text, "## What build writes, and when it refuses to overwrite"))
    inputs = _bullet_of(build, "The control totals on the inputs sheet")
    _all(inputs, ["follow the order of control totals", "under the agreement in force", "else the agreed contract's "
                  "total", "is not used: build prints a note that names it", "The cell's note names the source"],
         "model-spec-format.md, the inputs sheet's control totals")


@needs_plugin
def test_d1_check_and_lift_skills_state_the_order() -> None:
    check = _plain(_read(PLUGIN / "skills" / "check" / "SKILL.md"))
    step4 = _between(check, " 4. Accept the refreshed rows", " 5. D-KEY")
    _before(step4, ORDER, ORDER_STALE, "skills/check/SKILL.md step 4")
    _before(step4, ORDER_STALE, "Never the inputs sheet", "skills/check/SKILL.md step 4")
    lift = _plain(_read(PLUGIN / "skills" / "lift" / "SKILL.md"))
    _before(lift, ORDER, ORDER_STALE, "skills/lift/SKILL.md")
    _all(lift, ["lift takes no --control, so the script uses the total accepted for the snapshot it reads under the "
                "agreement in force"], "skills/lift/SKILL.md")


@pytest.mark.parametrize("path", _p8_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_p8_document_keeps_a_v8_statement_of_the_order(path: Path) -> None:
    text = _plain(_read(path))
    found = [t for t in V8_ORDER_TEXTS if t in text]
    assert not found, f"{path.name}: v8 statements of the order left: {found}"


# ----------------------------------------------------------------------------- D-2, D-3 [A-aera-4, A-aera-7]

def test_d2_control_needs_a_contract_total_in_column_contract_and_model_spec_format() -> None:
    later = _plain(_section(_read(REFS / "column-contract.md"), "## What the plugin does with the contract later"))
    _before(later, "--control needs --csv, and its table must be the table landed.", D2, "column-contract.md")
    bullet = _bullet_of(later, D2)
    assert bullet.rstrip() == D2, f"column-contract.md: D-2 is not its own bullet after the land --control bullet"
    landing = _plain(_section(_read(REFS / "model-spec-format.md"), "## Landing raw data"))
    _before(landing, ORDER_STALE, D2, "model-spec-format.md, Landing raw data")


@needs_plugin
def test_d2_control_needs_a_contract_total_in_the_check_skill() -> None:
    check = _plain(_read(PLUGIN / "skills" / "check" / "SKILL.md"))
    step4 = _between(check, " 4. Accept the refreshed rows", " 5. D-KEY")
    _before(step4, "--force --control <t>.<column>=<source total>", D2, "skills/check/SKILL.md step 4")
    _before(step4, D2, ORDER, "skills/check/SKILL.md step 4")


def test_d3_l_precision_bullet_states_key_uniqueness_on_stored_values() -> None:
    step = _step(2)
    bullet = _bullet_of(step, "L-PRECISION (a warning, one per column and reason")
    _before(bullet, "land the column as text (change its type in the contract and get the contract agreed again).",
            D3, "SKILL.md P2, the L-PRECISION bullet")
    assert bullet.rstrip().endswith(D3), f"SKILL.md P2: D-3 does not end the L-PRECISION bullet: {bullet[-200:]}"


# ----------------------------------------------------------------------------- D-4 [A-aera-1, C-sol-2]

def test_d4_skill_md_criteria_bullet_states_the_three_cases_and_both_patterns() -> None:
    step = _step(5)
    _before(step, "Build and check warn D-CRITERIA and name both values;", D4, "SKILL.md P5, the D-CRITERIA bullet")
    assert step.rstrip().endswith(D4), f"SKILL.md P5: D-4 does not end the D-CRITERIA bullet: {step[-300:]}"


def test_d4_checks_md_states_the_three_cases_and_both_patterns() -> None:
    text = _plain(_read(REFS / "checks.md"))
    para = _bullet_of(text, "A block that sums or counts by label")
    _before(para, "A label that is also a value of its column but that Excel's criteria matching does not match with "
                  "itself " + D4_BRACKET, D4_FIX, "checks.md, the criteria paragraph")
    _all(para, ["build and check warn D-CRITERIA with the reason self_mismatch and name the label and the first raw "
                "row.", "helper-patterns.md#a-label-that-excels-criteria-matching-does-not-match-with-itself"],
         "checks.md, the criteria paragraph")
    assert "the fix is a helper key with a fixed prefix on both sides" not in text, "checks.md keeps the v8 fix"
    item9 = _between(text, " 9. It warns D-CRITERIA", " 10. After a completed recalculation")
    _all(item9, [D4_ITEM9], "checks.md, What rotm.py check does, item 9")


# ----------------------------------------------------------------------------- D-5 [A-aera-11]

def test_d5_c_block_through_a_total_in_the_quick_reference_and_checks_md() -> None:
    row = _quick_row("to audit a workbook someone built by hand")
    _all(row, ["each calculation block that no check cell reads is C-BLOCK " + D5 + "; a run of links"],
         "SKILL.md quick reference, to audit a workbook someone built by hand")
    text = _plain(_read(REFS / "checks.md"))
    _all(text, ["each calculation block that no check cell reads " + D5 + ": two or more formula cells"],
         "checks.md, the C-BLOCK paragraph")


# ----------------------------------------------------------------------------- D-6 [C-sol-3, C-sol-7] part (2)

def test_d6_queries_a_hand_query_reads_are_compared_at_a_refresh_or_r6_says_they_are_not() -> None:
    """D-6 (full form) at its three places; or, after the integrator's swap for P2's reduced form, R-6 under 'Not
    verified' of SKILL.md and README.md and D-6 nowhere."""
    places = {"SKILL.md step 8": _step(8), "checks.md": _plain(_read(REFS / "checks.md"))}
    if HAS_PLUGIN:
        places["skills/check/SKILL.md"] = _plain(_read(PLUGIN / "skills" / "check" / "SKILL.md"))
    reduced = [p for p in _verified_files() if R6 in _plain(_section(_verified(p), "### Not verified"))]
    if reduced:
        assert len(reduced) == len(_verified_files()), "R-6 is under 'Not verified' of one file only"
        found = [where for where, text in places.items() if D6 in text]
        assert not found, f"R-6 is stated, but D-6 is still in {found}"
        return
    missing = [where for where, text in places.items() if D6 not in text]
    assert not missing, f"D-6 missing in {missing}"
    _before(places["SKILL.md step 8"], "A query changed in Excel's Power Query editor", D6, "SKILL.md step 8")
    _before(places["SKILL.md step 8"], D6, "I-HIDDEN-DECIMALS", "SKILL.md step 8")
    checks = places["checks.md"]
    _before(checks, "check --refresh also reads, through Excel and before the refresh, the formula of the query", D6,
            "checks.md")


# ----------------------------------------------------------------------------- D-7 [C-sol-7]

def test_d7_query_file_collision_is_refused_in_skill_md_and_model_spec_format() -> None:
    step = _step(2)
    rebuild = _between(step, "A rebuild over a query attached by hand", "A rebuild of a workbook whose raw sheet")
    with_excel = _between(rebuild, "With Excel:", "Without Excel, or when the M text")
    _before(with_excel, T3_FULL_END, D7, "SKILL.md P2, With Excel")
    spec = _plain(_section(_read(REFS / "model-spec-format.md"), "## Raw sheets"))
    _before(spec, "queries/<name>.hand.pq.m", D7, "model-spec-format.md, Raw sheets")


# ----------------------------------------------------------------------------- D-8 [C-sol-5]

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_d8_finish_check_row_ends_with_the_stale_pass(path: Path) -> None:
    row = _hooks_row(path, "finish check")
    _before(row, T8_V8, D8, f"{path.name} hooks table, finish check")
    assert row.rstrip(" |").endswith(D8), f"{path.name} hooks table: D-8 does not end the finish check row"


# ----------------------------------------------------------------------------- D-9 [A-aera-5]

def test_d9_diff_fields_in_the_quick_reference() -> None:
    row = _quick_row("what changed between two versions")
    _before(row, "ROTM diff old.xlsx new.xlsx", D9, "SKILL.md quick reference, what changed between two versions")


@needs_plugin
def test_d9_diff_fields_in_the_readme_commands_table() -> None:
    rows = [ln for ln in _section(_read(PLUGIN / "README.md"), "## Commands").splitlines()
            if ln.startswith("| `/rule-of-thumb-model:diff` |")]
    assert len(rows) == 1, f"README.md commands table: {len(rows)} rows for diff"
    _before(_plain(rows[0]), "lists the formulas, inputs and outputs that changed between two versions", D9,
            "README.md commands table, diff")


# ----------------------------------------------------------------------------- D-10 [A-aera-9, A-aera-12]

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_d10_is_stated_only_where_the_file_says_what_compat_reads_outside_cells(path: Path) -> None:
    """D-10 is added only where a file states what compat reads in validations, conditional formats or defined
    names. Neither SKILL.md nor README.md does (P8 v9 change note), so neither holds D-10."""
    text = _plain(_read(path))
    states = re.search(r"compat[^.|]*(data validation|conditional-format rule|defined name)", text) is not None
    assert (D10 in text) == states, f"{path.name}: D-10 {'missing' if states else 'added'} " \
                                    f"(the file {'states' if states else 'does not state'} what compat reads there)"


# ----------------------------------------------------------------------------- D-11 and [J-1] template folders

def test_d11_step_0_and_the_quick_reference_say_where_the_template_folders_are() -> None:
    step = _step(0)
    bullet = _bullet_of(step, "From a template:")
    _before(bullet, "its user must agree them again (P1).", D11, "SKILL.md step 0, From a template")
    assert bullet.rstrip().endswith(D11), f"SKILL.md step 0: D-11 does not end the bullet: {bullet[-200:]}"
    row = _quick_row("start from a known model")
    _before(row, "ROTM init MODEL --template budget_vs_actual", "(" + D11 + ")",
            "SKILL.md quick reference, start from a known model")


def test_init_list_templates_prints_an_existing_folder_for_each_template(tmp_path: Path) -> None:
    """[J-1]: in this tree (the plugin, or the skill folder copied alone) init --list-templates prints each template's
    name and its folder; the folder exists, holds model.spec.json and has the template's name. starter lies in the
    skill folder; the other four lie in the plugin's templates folder and are listed only with the plugin."""
    listed = _list_templates(SCRIPTS / "rotm.py", tmp_path)
    names = [n for n, _ in listed]
    expected = ["starter"] + (list(PLUGIN_TEMPLATES) if HAS_PLUGIN else [])
    assert sorted(names) == sorted(expected), f"init --list-templates lists {names}, expected {expected}"
    for name, folder in listed:
        assert folder.is_dir() and (folder / "model.spec.json").is_file(), f"{name}: {folder} is no template folder"
        assert folder.name == name, f"{name}: the folder {folder} has another name"
        home = SKILL / "templates" if name == "starter" else PLUGIN / "templates"
        assert _same(folder, home / name), f"{name}: {folder} is not {home / name}"


def test_init_list_templates_in_the_skill_folder_copied_alone_lists_only_starter(
        outside_tmp: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """[J-1]: the skill folder copied alone (as selftest step f02 does, without examples and tests) lists only the
    templates present there: starter, with its existing folder inside the copy."""
    from lib import selftest
    alone = outside_tmp / "k" / "s"
    skip = shutil.ignore_patterns(*selftest.COPY_SKIP, "reports", "examples", "tests", "render", "lift")
    session = tmp_path_factory.getbasetemp().resolve()

    def ignore(folder: str, names: list[str]) -> set[str]:
        # with --basetemp inside the skill folder the copy would hold this session's temporary folder; it is left out
        return set(skip(folder, names)) | {n for n in names if _inside(Path(folder) / n, session)}

    shutil.copytree(SKILL, alone, ignore=ignore)
    assert not (alone.parents[1] / ".claude-plugin" / "plugin.json").exists()
    listed = _list_templates(alone / "scripts" / "rotm.py", outside_tmp)
    assert [n for n, _ in listed] == ["starter"], f"the skill folder alone lists {listed}"
    folder = listed[0][1]
    assert folder.is_dir() and (folder / "model.spec.json").is_file(), folder
    assert _same(folder, alone / "templates" / "starter"), f"{folder} is not the copy's starter folder"


def test_init_from_a_template_copies_its_readme_contracts_and_specification(outside_tmp: Path) -> None:
    """D-11: a template's README, contracts and specification are read in MODEL after init has copied them there."""
    model = outside_tmp / "m"
    proc = subprocess.run(PY + [str(SCRIPTS / "rotm.py"), "init", str(model), "--template", "starter"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(outside_tmp),
                          timeout=120)
    assert proc.returncode == 0, (proc.stdout[-2000:], proc.stderr[-2000:])
    src = SKILL / "templates" / "starter"
    for rel in ("README.md", "model.spec.json"):
        assert (model / rel).read_bytes() == (src / rel).read_bytes(), f"{rel} was not copied into MODEL as it is"
    contracts = sorted(p.name for p in (src / "contracts").glob("*.contract.json"))
    assert contracts and all((model / "contracts" / c).is_file() for c in contracts), contracts


# ----------------------------------------------------------------------------- D-12 the statements P8 changed

def test_d12_statements_made_untrue_by_an_entry_are_changed() -> None:
    _all(_plain(_section(_skill_md(), "## What is and is not verified")) + " " + _step(8), D12_SKILL, "SKILL.md")
    if HAS_PLUGIN:
        _all(_plain(_section(_read(PLUGIN / "README.md"), "## What is and is not verified")), D12_README,
             "README.md")
        check = _plain(_read(PLUGIN / "skills" / "check" / "SKILL.md"))
        _all(check, ["a rebuild then keeps it while the contract is not agreed again",
                     "Running it again under the same agreement gives the same record."], "skills/check/SKILL.md")
    checks = _plain(_read(REFS / "checks.md"))
    _all(checks, ["build and check take the control total accepted for the snapshot they read under the agreement "
                  "in force from that file, so a rebuild keeps the accepted total while the contract is not agreed "
                  "again", "--control is accepted only for a column whose control total the agreed contract names; "
                  "for another column the acceptance is refused (exit 2) and nothing is recorded"], "checks.md")


# ----------------------------------------------------------------------------- [J-2] what is and is not verified

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_runs_on_the_final_v9_files_holds_the_line_or_runs(path: Path) -> None:
    section = _verified(path)
    assert RUNS_V9 in section, f"{path.name}: no subsection '{RUNS_V9}'"
    for old in ("v3", "v4", "v5", "v6", "v7", "v8"):
        assert f"### Runs on the final {old} files" not in section, f"{path.name}: the {old} subsection is left"
    head = section[:section.index(RUNS_V9)]
    assert VERSION_V9.search(head), f"{path.name}: the version line does not say 'Version v9 of <date>.'"
    _all(_flat(head), HEAD_V9, f"{path.name}, What is and is not verified")
    bullets = _run_bullets(section)
    assert bullets, f"{path.name}: the subsection '{RUNS_V9}' holds no bullet"
    if bullets == [P_RUNS_V9]:
        return                                              # before the integrator's runs: only the integrator's line
    assert P_RUNS_V9 not in " ".join(bullets), f"{path.name}: the integrator's line is left beside the runs"
    for bullet in bullets:
        if bullet.startswith("Not run"):
            continue
        assert re.search(r"20\d\d-\d\d-\d\d", bullet) and re.search(r"\b\d\d:\d\d\b", bullet), \
            f"{path.name}: a run without its date and time: {bullet[:120]}"
        assert re.search(r"\bexit \d\b|\bpassed\b|\bfailed\b", bullet), \
            f"{path.name}: a run without its result: {bullet[:120]}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_runs_on_the_final_v9_files_name_the_required_runs(path: Path) -> None:
    """Verdict [J-2]: the whole suite without Excel in the three temporary-folder set-ups (each basetemp folder
    removed before the next run), the Excel tests of the changed modules, selftest --quick and the selftest steps
    f02 and f27 alone, each with its command, result, test count (where it has one), date and time; and the full
    strict selftest, or the statement that the builder did not run it. No v8 text in the subsection."""
    section = _verified(path)
    if _before_the_runs(section):
        pytest.skip("the integrator has not written the runs on the final v9 files yet")
    bullets = _run_bullets(section)
    runs = _flat(" ".join(bullets))
    _all(runs, REQUIRED_RUNS, f"{path.name}, runs on the final v9 files")
    assert runs.count("--basetemp") >= 2, f"{path.name}: the two --basetemp runs are not both stated"
    assert "v8" not in _section(section, RUNS_V9), f"{path.name}: the runs on the final v9 files name v8"
    dated = [b for b in bullets if not b.startswith("Not run")]
    for bullet in dated:
        assert "`" in bullet, f"{path.name}: a run without its command: {bullet[:120]}"
        if "pytest" in bullet or "selftest --quick" in bullet:
            assert re.search(r"\b\d[\d,]* passed\b", bullet), f"{path.name}: a run without its test count: " \
                                                               f"{bullet[:120]}"
    strict_ran = any("selftest --strict" in b for b in dated)
    strict_not_run = any(b.startswith("Not run by the builder") and "selftest --strict" in b for b in bullets)
    assert strict_ran or strict_not_run, \
        f"{path.name}: neither a run of the full strict selftest nor 'Not run by the builder' for it"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_no_v8_run_statement_is_carried_over(path: Path) -> None:
    text = _flat(_verified(path))
    found = [t for t in V8_RUN_TEXTS if t in text]
    assert not found, f"{path.name}: v8 run statements left: {found}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_v9_verified_items_name_their_tests(path: Path) -> None:
    """Each v9 item of plan 5.5 starts its own bullet with its title and names its fixed tests (plan 5.3), each of
    which exists in the test files (they are written by P2, P3 and P6: plan section 7, I-1). Before the integrator's
    runs every item is there with the integrator's sentence; after them no sentence is left, and an item not kept is
    named under 'Not verified'. No v9 item names Google Sheets."""
    section = _verified(path)
    verified_text = _section(section, "### Verified, with the tests that show it")
    verified = _flat(verified_text)
    not_verified = _flat(_section(section, "### Not verified"))
    bullets = _bullets(verified_text)
    present = [item for item in V9_VERIFIED if item in verified]
    missing_tests: list[str] = []
    for item in present:
        own = [b for b in bullets if b.startswith(item)]
        assert len(own) == 1, f"{path.name}: {len(own)} bullets start with the v9 item '{item}'"
        missing = [t for t in V9_VERIFIED[item]
                   if (t.split("::")[0], t.split("::")[1]) not in _named_tests(own[0])]
        assert not missing, f"{path.name}: the verified item '{item}' does not name {missing}"
        assert "Google" not in own[0], f"{path.name}: the v9 item '{item}' names Google"
        missing_tests += [f"{f}::{n}" for f, n in _named_tests(own[0]) if not _test_exists(f, n)]
    if _before_the_runs(section):              # before the integrator's runs: every item, each with its sentence
        assert present == list(V9_VERIFIED), f"{path.name}: v9 items missing: {set(V9_VERIFIED) - set(present)}"
        for item in present:
            own = next(b for b in bullets if b.startswith(item))
            assert own.count(KEEP) == 1 and own.rstrip().endswith(KEEP), \
                f"{path.name}: the v9 item '{item}' does not end with the integrator's sentence"
        assert verified.count(KEEP) == len(V9_VERIFIED), f"{path.name}: {verified.count(KEEP)} sentences"
    else:                                      # after them: the items kept, no sentence; the others not verified
        assert KEEP not in verified, f"{path.name}: the integrator's sentence is left"
        for item in V9_VERIFIED:
            if item not in present:
                name = item.replace(" (v9)", "")
                assert name in not_verified, f"{path.name}: the v9 item '{name}' is in neither list"
    assert not missing_tests, f"{path.name}: the v9 items name tests that do not exist: {missing_tests}"


@needs_plugin
def test_skill_md_and_readme_list_the_same_v9_items() -> None:
    def titles(path: Path) -> list[str]:
        verified = _flat(_section(_verified(path), "### Verified, with the tests that show it"))
        return [item for item in V9_VERIFIED if item in verified]
    assert titles(SKILL / "SKILL.md") == titles(PLUGIN / "README.md")

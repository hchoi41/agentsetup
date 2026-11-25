"""Tests of the v4 documents (package P8 of the v4 round, 2026-11-25). Owner: P8.

- [A-aera-16, C-sol-7, C-sol-8]: references/google-sheets.md lands the first extract before the build, gives the
  repair of the helper rows after a refresh to more, fewer and zero rows, and states the export route for a refresh
  made in Google Sheets. The route itself is tested here without Google Sheets and without Excel: a static model, a
  copy of its workbook whose raw rows are those of a second CSV (in place of the download), that CSV landed with
  --force in a copy of the model folder, then check --strict.
- [J-3]: SKILL.md and README.md "What is and is not verified": the four v4 items of the verified list with their
  tests, Google Sheets only under "Not verified". v5 (J-3): the subsection "Runs on the final v4 files" became
  "Runs on the final v5 files", and no v3 or v4 run number is left. v6 (J-3): it became "Runs on the final v6
  files" (test_v6_docs.py checks its contents).
- v5 ([A-aera-6, C-sol-3]): the test that documented the dead end of a changed source total is replaced by a
  document test of the new step 4 of "Models kept in Google Sheets" (land --control); test_v5_land.py tests the
  behaviour.
- The documents of the other packages' v4 changes (plan section 6.8): X-OPEN and F-PARSE, D-CRITERIA, the marker's
  workbook, the summary lines, ALERT and C-ALERT, the guarded-lookup split, a reply that postpones, names in a
  contract, the lift tolerance, the hand-attached query over a rebuild, D-TYPE for a text column.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v4_docs.py -q -p no:cacheprovider
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
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

PY = [sys.executable, "-P", "-X", "utf8", "-B"]
ROTM = PY + [str(SCRIPTS / "rotm.py")]

# The bullet the integrator replaced with the runs made on the final v4 files (plan section 6.8, item 2).
P_RUNS = ("Not yet stated: the integrator writes here each run made on the final v4 files (command, result, test "
          "count, date and time).")
RUNS_V4 = "### Runs on the final v4 files"
# v5 (J-3): the subsection that replaces it, and the line the integrator replaces with the runs on the v5 files.
RUNS_V5 = "### Runs on the final v5 files"
P_RUNS_V5 = "Integrator: replace this line with the runs on the final v5 files."
# v6 (J-3): the subsection that replaces the v5 one, and its placeholder (test_v6_docs.py checks its contents).
RUNS_V6 = "### Runs on the final v6 files"
P_RUNS_V6 = "Integrator: replace this line with the runs on the final v6 files."

# Numbers and words of the v3 runs that v4 must not carry (J-3).
V3_RUN_TEXTS = ("2,238", "2,344", "34 minutes 39 seconds", "15:55 to 16:00", "15:17 to 15:52", "292 files",
                "7,076", "57,717,000", "Version v3", "on the final v3 files", "for v3 only", "23 of 23")
# v5 (J-3): numbers and words of the v4 runs that v5 must not carry.
V4_RUN_TEXTS = ("2,391", "2,085", "19:32 to 19:41", "19:28 to 20:13", "19:41 to 19:52", "19:41 to 19:48",
                "19:14 to 19:16", "19:11 to 19:13", "Version v4", "on the final v4 files", "for v4 only")

# The tests the verified list names for the four v4 items (plan section 5.4).
V4_VERIFIED_TESTS = {
    "a hand-attached query carried over a rebuild": [
        "test_v4_build.py::test_hand_attached_query_is_carried_over_a_rebuild",
        "test_v4_build.py::test_rebuild_over_a_hand_attached_query_without_excel_is_refused"],
    "the first acceptance of a hand-attached query": [
        "test_v4_check.py::test_first_acceptance_of_a_hand_attached_query_records_it"],
    "the criteria warning": [
        "test_v4_check.py::test_criteria_match_follows_excel_countifs",
        "test_v4_check.py::test_check_warns_for_criteria_labels_that_excel_merges",
        "test_v4_check.py::test_build_warns_for_criteria_labels_that_excel_merges"],
    "the guarded-lookup split": [
        "test_v4_split.py::test_guarded_lookup_split_keeps_the_missing_key_result"],
}


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


def _all(text: str, needed: list[str] | tuple[str, ...], where: str) -> None:
    missing = [n for n in needed if n not in text]
    assert not missing, f"{where}: missing {missing}"


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


def _gs(heading: str) -> str:
    return _flat(_section(_read(REFS / "google-sheets.md"), heading))


def _command_skill(name: str) -> str:
    return _flat(_read(PLUGIN / "skills" / name / "SKILL.md"))


def _verified_files() -> list[Path]:
    return [SKILL / "SKILL.md"] + ([PLUGIN / "README.md"] if HAS_PLUGIN else [])


def _verified(path: Path) -> str:
    return _section(_read(path), "## What is and is not verified")


# ----------------------------------------------------------------------------- [C-sol-8] the export route

def _rotm(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, ROTM_NO_EXCEL="1")
    return subprocess.run(ROTM + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=600, cwd=cwd)


def _report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rules(report: dict, severity: str | None = None) -> list[str]:
    return [f["rule"] for f in report["findings"] if severity is None or f["severity"] == severity]


@pytest.fixture(scope="module")
def static_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The starter template (init --template starter), its contract shown and agreed, its sample landed, built
    without Excel: a static model, as a model converted to Google Sheets starts."""
    root = tmp_path_factory.mktemp("gs_route") / "model"
    for args in (("init", str(root), "--template", "starter"),
                 ("contract", "show", str(root), "--table", "lines"),
                 ("contract", "agree", str(root), "--table", "lines", "--reply", "agreed", "--by", "tester"),
                 ("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv")),
                 ("build", str(root), "--strict")):
        proc = _rotm(*args)
        assert proc.returncode == 0, (args, proc.stdout[-2000:], proc.stderr[-2000:])
    return root


def _export(model: Path, folder: Path, *, keep_total: bool) -> tuple[Path, list[dict]]:
    """The CSV export of the refreshed extract: the landed rows with changed amounts and the same row count; with
    keep_total the changes cancel out, so the total equals the contract's control total."""
    with open(model / "data" / "lines.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    rows[0]["amount"] = str(int(rows[0]["amount"]) + 1000)
    if keep_total:
        rows[1]["amount"] = str(int(rows[1]["amount"]) - 1000)
    path = folder / "export_lines.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path, rows


def _copy_of_the_model_folder(model: Path, copy: Path) -> Path:
    """google-sheets.md 'Models kept in Google Sheets' step 3.1: contracts and raw first, then the manifest, the
    specification and the marker; no workbook."""
    copy.mkdir(parents=True)
    for name in ("contracts", "raw"):
        shutil.copytree(model / name, copy / name)
    for name in ("model.manifest.json", "model.spec.json", "rotm_model.json"):
        shutil.copy2(model / name, copy / name)
    assert not (copy / "model.xlsx").exists()
    return copy


def _save_download(model: Path, copy: Path, rows: list[dict]) -> Path:
    """The download in place of Google Sheets': the built workbook whose raw rows are those of the export,
    saved in the copy under the marker's workbook name."""
    import openpyxl
    name = json.loads((copy / "rotm_model.json").read_text(encoding="utf-8"))["workbook"]
    wb = openpyxl.load_workbook(model / "model.xlsx")
    ws = wb["raw_lines"]
    header = [c.value for c in ws[1]]
    col = header.index("amount") + 1
    for i, r in enumerate(rows, start=2):
        ws.cell(row=i, column=col).value = int(r["amount"])
    out = copy / name
    wb.save(out)
    return out


def test_google_sheets_export_route_lands_and_checks(static_model: Path, tmp_path: Path) -> None:
    """[C-sol-8]: the export of a refreshed extract whose total equals the contract's control total is landed with
    --force in a copy of the model folder; check --strict of the download then reports no R-CHANGED and no error,
    and the landing record names the export."""
    export, rows = _export(static_model, tmp_path, keep_total=True)
    copy = _copy_of_the_model_folder(static_model, tmp_path / "review_copy")
    landed = _rotm("land", str(copy), "--table", "lines", "--csv", str(export), "--force")
    assert landed.returncode == 0, landed.stdout[-2000:]
    record = _report(copy / "reports" / "status.json")["workbooks"]
    land_step = next(v["land"] for v in record.values() if "land" in v)
    assert land_step["source"].endswith("export_lines.csv") and land_step["rows"] == len(rows), land_step
    _save_download(static_model, copy, rows)
    out = tmp_path / "check.json"
    checked = _rotm("check", str(copy), "--strict", "--json", str(out))
    report = _report(out)
    assert "R-CHANGED" not in _rules(report), checked.stdout[-3000:]
    assert not _rules(report, "error"), checked.stdout[-3000:]
    assert checked.returncode == 3, checked.stdout[-3000:]          # no Excel here: not recalculated
    assert "not recalculated" in checked.stdout
    audit = _rotm("audit", str(copy), "--strict")
    assert audit.returncode == 0, audit.stdout[-3000:]
    assert (static_model / "raw" / "lines.csv").read_bytes() != (copy / "raw" / "lines.csv").read_bytes()


def test_google_sheets_step_4_lands_the_export_with_the_source_total() -> None:
    """v5 [A-aera-6, C-sol-3], in place of the v4 test that documented the dead end: step 4 of "Models kept in
    Google Sheets" types the new total into the control input cell before the download, lands the export with
    --force and --control, saves the download in the copy and runs check --strict, says what D-CONTROL-INPUT and
    H-COVERAGE or H-BEYOND mean then, and is marked as tested with a local stand-in only. test_v5_land.py tests
    the behaviour."""
    kept = _section(_read(REFS / "google-sheets.md"), "## Models kept in Google Sheets")
    m = re.search(r"^4\. (.*?)^5\. ", kept, flags=re.M | re.S)
    assert m, "google-sheets.md: no step 4 in 'Models kept in Google Sheets'"
    step = _flat(m.group(1))
    _all(step, ["before the download", "control input cell", "Export the refreshed extract's sheet `raw_<t>` as a CSV",
                "rotm.py land <copy of the model folder> --table <t> --csv <export> --force --control "
                "<t>.<column>=<source total>", "save the download in the copy",
                "rotm.py check <copy of the model folder> --strict", "D-CONTROL-INPUT", "the cell was not updated",
                "H-COVERAGE or H-BEYOND", "What a refresh does to the formulas that read an extract",
                "not tested in Google Sheets", "tested here with a local stand-in", "L-TOTAL",
                "recorded the source total <column> = <value> for this snapshot in raw/<t>.controls.json"],
         "google-sheets.md, Models kept in Google Sheets step 4")
    assert step.index("control input cell") < step.index("--control <t>.<column>") < step.index("--strict"), \
        "the order of step 4: the cell, the land, the check"
    assert "The existing commands do not accept a refreshed static table with a new source total." not in kept


# ----------------------------------------------------------------------------- [A-aera-16, C-sol-7, C-sol-8] text

def test_google_sheets_lands_the_first_extract_before_the_build() -> None:
    steps = _gs("## Landing a raw table: the steps")
    _all(steps, ["queries/<t>.sql", "BigQuery", "staging extract", "row count", "control total",
                 "rotm.py land MODEL --table <t> --csv <file>", "raw/<t>.csv", "Only then",
                 "stands in for the extract until the cutover", "not tested in Google Sheets"],
         "google-sheets.md, Landing a raw table")
    assert steps.index("--csv <file>") < steps.index("rotm.py build"), "the first extract is landed before the build"
    numbers = [int(n) for n in re.findall(r"(?:^|\s)(\d+)\. ", _section(_read(REFS / "google-sheets.md"),
                                                                       "## Landing a raw table: the steps"))]
    assert numbers == list(range(1, len(numbers) + 1)) and len(numbers) == 10, numbers
    cut = _gs("## Cutover when the extract is on a new sheet")
    _all(cut, ["(step 5)", "(steps 6 to 8)", "the first extract for that table name (steps 2 and 3"],
         "google-sheets.md, cutover step 2")


def test_google_sheets_refresh_repair_covers_more_fewer_and_zero_rows() -> None:
    text = _gs("## What a refresh does to the formulas that read an extract")
    _all(text, ["More raw rows", "Fewer raw rows", "Zero raw rows", "copy the last helper row",
                "clear the contents of the helper rows below the last raw row", "leave the rows above untouched",
                "row 2", "notes sheet", "model.manifest.json", "write them back", "<t>_cover", "<t>_beyond",
                "master check", "not tested in Google Sheets", "rotm.py extend", "tested in both directions",
                "test_v2_core.py::test_power_query_raw_table_keeps_its_formats_through_attach_growth_and_shrink",
                "test_v3_raw.py::test_empty_table_through_power_query_one_zero_one"],
         "google-sheets.md, What a refresh does")
    _all(_gs("## Max's test in Google Sheets"), ["zero rows"], "google-sheets.md, Max's test step 3")


def test_google_sheets_states_the_export_route_and_what_check_reports() -> None:
    kept = _gs("## Models kept in Google Sheets")
    _all(kept, ["export", "--csv <export> --force", "reports/status.json", "names the export file",
                "calculated by Google Sheets", "L-TOTAL", "D-CONTROL-INPUT", "--control <t>.<column>=<source total>",
                "no R-CHANGED", "test_v4_docs.py::test_google_sheets_export_route_lands_and_checks",
                "test_v5_land.py::test_wrong_control_is_l_total_and_writes_nothing",
                "not tested in Google Sheets", "accepts changed raw rows only from a Power Query table"],
         "google-sheets.md, Models kept in Google Sheets")
    assert "test_v4_docs.py::test_google_sheets_export_route_with_a_changed_total" not in kept


# ----------------------------------------------------------------------------- [J-3] what is and is not verified

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_runs_on_the_final_v4_files_holds_the_placeholder_or_runs(path: Path) -> None:
    """v5 and v6 (J-3): the subsection on the v4 files was replaced by the one on the v5 files, and that one by the
    one on the v6 files, which holds the v6 placeholder or the integrator's dated runs."""
    section = _verified(path)
    assert RUNS_V4 not in section and "### Runs on the final v3 files" not in section
    assert RUNS_V5 not in section
    assert RUNS_V6 in section
    runs = _flat(_section(section, RUNS_V6))
    assert P_RUNS not in runs and P_RUNS_V5 not in runs, f"{path.name}: an earlier placeholder is left"
    assert P_RUNS_V6 in runs or re.search(r"20\d\d-\d\d-\d\d", runs), \
        f"{path.name}: the subsection holds neither the placeholder nor a dated run"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_no_v3_run_statement_is_carried_over(path: Path) -> None:
    """v5 (J-3): no v4 run statement either."""
    text = _flat(_verified(path))
    found = [t for t in V3_RUN_TEXTS + V4_RUN_TEXTS if t in text]
    assert not found, f"{path.name}: v3 or v4 run statements left: {found}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_verified_list_names_the_v4_items_with_their_tests(path: Path) -> None:
    section = _verified(path)
    verified = _flat(_section(section, "### Verified, with the tests that show it"))
    not_verified = _flat(_section(section, "### Not verified"))
    for item, tests in V4_VERIFIED_TESTS.items():
        missing = [t for t in tests if t not in verified]
        assert not missing, f"{path.name}: the verified item '{item}' does not name {missing}"
    assert "Google" not in verified, f"{path.name}: a Google Sheets statement in the verified list"
    assert "Google Sheets" in not_verified
    assert "is read by no check" not in not_verified, "the worked example's var_pct row has a check in v4"


# ----------------------------------------------------------------------------- [C-sol-3] X-OPEN and F-PARSE

def test_x_open_and_f_parse_in_skill_md() -> None:
    _all(_step(7), ["X-OPEN", "could not open or calculate", "exit 1", "audit", "--strict", "F-PARSE",
                    "--no-excel", "not recalculated"], "SKILL.md P7")
    _all(_step(4), ["F-PARSE", "trailing", "exit 2", "JSON path"], "SKILL.md P4")


@needs_plugin
def test_x_open_in_the_check_skill() -> None:
    _all(_command_skill("check"), ["X-OPEN", "could not open or calculate", "exit 1", "F-PARSE",
                                   "--no-excel", "busy"], "skills/check/SKILL.md")


def test_check_docs_name_x_open_and_d_criteria() -> None:
    text = _flat(_read(REFS / "checks.md"))
    intro = text[:text.index("## Evidence labels")]
    _all(intro, ["X-OPEN", "D-CRITERIA"], "checks.md, when to read it")
    section = text[text.index("## What `rotm.py check` does"):text.index("## Common mistakes")]
    _all(section, ["X-OPEN", "could not open or calculate", "D-CRITERIA", "warning"], "checks.md, check")
    builds = text[text.index("## Checks you add in the specification"):text.index("## Tolerance")]
    _all(builds, ["D-CRITERIA"], "checks.md, checks you add")


# ----------------------------------------------------------------------------- [A-aera-1] criteria labels

def test_helper_patterns_describe_labels_that_excel_criteria_merge() -> None:
    text = _read(REFS / "helper-patterns.md")
    heading = "## Labels that Excel's criteria matching merges"
    assert heading in text
    sec = _flat(_section(text, heading))
    _all(sec, ["SUMIFS", "COUNTIFS", "'001'", "'1'", "case", "`*`", "`?`", "`=`, `<` or `>`", "D-CRITERIA",
               "adds both", "source expression", "helper column", "build", "check", "warning"],
         "helper-patterns.md, criteria labels")


# ----------------------------------------------------------------------------- [C-sol-9] the marker's workbook

def test_p7_uses_the_markers_workbook() -> None:
    step = _step(7)
    _all(step, ["rotm_model.json", "`output`", "the workbook the build report names"], "SKILL.md P7")
    assert "ROTM render MODEL/model.xlsx" not in step


# ----------------------------------------------------------------------------- [C-sol-5] summary lines

SUMMARY_WORDS = ["not calculated (0 of N have a value)", "a number other than 0", "(cached values)"]


def test_summary_lines_in_skill_md_and_checks_md() -> None:
    _all(_step(7), SUMMARY_WORDS, "SKILL.md P7")
    text = _flat(_read(REFS / "checks.md"))
    _all(text[text.index("8. It prints"):], SUMMARY_WORDS, "checks.md, check step 8")


@needs_plugin
def test_summary_lines_in_the_check_skill() -> None:
    _all(_command_skill("check"), SUMMARY_WORDS, "skills/check/SKILL.md")


# ----------------------------------------------------------------------------- [C-sol-12, C-sol-13, J-1] ALERT

def test_alert_rows_show_alert_in_the_documents() -> None:
    looks = _flat(_section(_read(REFS / "checks.md"), "## How a check looks"))
    _all(looks, ['[Red]"ALERT";[Red]"ALERT";"OK"', "ALERT", "never FAIL", "C-ALERT", "info"],
         "checks.md, How a check looks")
    _all(_step(5), ["ALERT", "never `FAIL`", "C-ALERT"], "SKILL.md P5")
    gs = _flat(_read(REFS / "google-sheets.md"))
    assert '[Red]"ALERT";[Red]"ALERT";"OK"' in gs


@needs_plugin
def test_alert_word_in_the_check_skill_and_the_template_library() -> None:
    _all(_command_skill("check"), ["ALERT", "C-ALERT"], "skills/check/SKILL.md")
    lib = _flat(_read(PLUGIN / "templates" / "README.md"))
    assert "24 of 24 hold" in lib and "23 of 23" not in lib


# ----------------------------------------------------------------------------- [A-aera-14, A-aera-5] split

def test_split_docs_state_the_guarded_lookup_scope() -> None:
    text = _read(REFS / "helper-patterns.md")
    h5 = _flat(_section(text, "## H5. A lookup with a not-found fallback (IFERROR > INDEX > MATCH)"))
    _all(h5, ["rotm.py split", "IFERROR or IFNA", "SPLIT-GUARD", "nothing is written"], "helper-patterns.md H5")
    split = _flat(_section(text, "## How to split a formula, in order"))
    _all(split, ["INDEX", "MATCH", "H5", "SPLIT-GUARD", "whatever the current values are", "text literals"],
         "helper-patterns.md, the split bullet")
    _all(_step(4), ["H5", "IFERROR or IFNA", "SPLIT-GUARD", "nothing written", "text literals"], "SKILL.md P4")


# ----------------------------------------------------------------------------- [A-aera-4] a reply that postpones

def test_a_postponing_reply_is_refused_in_skill_md() -> None:
    _all(_step(1), ["postpones the decision", "no agreement was given", "wait for the user's reply", "나중에"],
         "SKILL.md P1 step 6")


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_hooks_tables_refuse_a_reply_that_postpones(path: Path) -> None:
    heading = "## Hooks" + (" (when the plugin is installed)" if path.name == "SKILL.md" else "")
    hooks = _flat(_section(_read(path), heading))
    row = hooks.split("| contract gate |", 1)[1].split("| raw-sheet guard |", 1)[0]
    _all(row, ["postpones"], f"{path.name} hooks table, contract gate")


# ----------------------------------------------------------------------------- [A-aera-11, A-aera-12] names

def test_p1_states_quoted_identifiers_and_helper_names() -> None:
    _all(_step(1), ["quoted identifier", "reserved word", "Order Date", "N-HELPER-NAME", "in_raw", "dup_key",
                    "_src", "exit 2"], "SKILL.md P1")


# ----------------------------------------------------------------------------- [A-aera-6, J-2] lift

def test_lift_row_states_the_absolute_tolerance_and_the_largest_difference() -> None:
    from lib import lift                       # the row states the factor the code uses (request v4_P6 R1)
    qr = _flat(_section(_skill_md(), "## Quick reference"))
    row = qr.split("| the model as a Python script |", 1)[1].split("|", 2)[0]
    _all(row, ["absolute", f"{lift.REL_TOLERANCE:g} times the larger value", "largest difference",
               "--json"], "SKILL.md quick reference, lift")


# ----------------------------------------------------------------------------- [C-sol-1], [A-aera-3, C-sol-2, C-sol-6]

def test_skill_md_says_what_a_rebuild_does_to_a_hand_attached_query() -> None:
    _all(_step(2), ["queries/<t>.hand.pq.m", "--drop-hand-queries", "carried over", "Excel", "refused",
                    "first load equals the landed snapshot"], "SKILL.md P2")


def test_acceptance_needs_a_live_query_table_and_the_agreed_contract() -> None:
    words = ["no query table", "nothing is accepted", "CT-VERSION"]
    _all(_step(8), words, "SKILL.md step 8")
    text = _flat(_read(REFS / "checks.md"))
    _all(text[text.index("## What `rotm.py check` does"):text.index("## Common mistakes")],
         words + ["first load equals the landed snapshot"], "checks.md, check")


@needs_plugin
def test_acceptance_rules_in_the_check_skill() -> None:
    _all(_command_skill("check"), ["no query table", "CT-VERSION", "first load equals the landed snapshot"],
         "skills/check/SKILL.md")


# ----------------------------------------------------------------------------- [A-aera-8, A-aera-9] D-TYPE

def test_d_type_names_a_text_column_stored_as_numbers() -> None:
    words = ["text column", "as a number", "901"]
    _all(_step(8), words, "SKILL.md step 8")
    text = _flat(_read(REFS / "checks.md"))
    _all(text[text.index("## What `rotm.py check` does"):text.index("## Common mistakes")], words, "checks.md")
    if HAS_PLUGIN:
        _all(_command_skill("check"), words, "skills/check/SKILL.md")


# ----------------------------------------------------------------------------- integrator: the packages' requests

def test_f_parse_documents_name_no_empty_argument() -> None:
    """Request v4_P1 R1: Excel 16.0 opened every empty argument that was measured, so F-PARSE refuses none
    (data/formula/incomplete_examples.json) and the documents do not list one."""
    data = json.loads((SCRIPTS / "tests" / "data" / "formula" / "incomplete_examples.json").read_text(encoding="utf-8"))
    assert {"=ROUND(A1,)", "=SUM(,)"} <= {e["formula"] for e in data["accepted"]}
    assert "empty argument" not in _step(4), "SKILL.md P4"
    if HAS_PLUGIN:
        assert "empty argument" not in _command_skill("check"), "skills/check/SKILL.md"


def test_hook_table_reads_not_yet_as_a_postponement() -> None:
    """Request v4_P4 R-2: contract.reads_as_refusal reads 'not yet' and '아직' as a reply that postpones, so the
    hook table does not list them among the negated agreements."""
    from lib import contract
    for reply in ("not yet", "아직 동의하지 않았습니다"):
        assert (contract.reads_as_refusal(reply) or "").startswith(contract.POSTPONE_REASON), reply
    hooks = _section(_skill_md(), "## Hooks (when the plugin is installed)")
    row = next(line for line in hooks.splitlines() if line.startswith("| contract gate |"))
    negated = row.split("negated agreement anywhere:", 1)[1].split(")", 1)[0]
    assert "not yet" not in negated and "아직" not in negated, negated


def test_power_query_reference_states_the_quoted_source_identifier() -> None:
    """Request v4_P4 R-3: a source expression that is one name in backticks gets the connector's quotes."""
    from lib import land
    c = {"table": "t", "columns": [{"name": "order", "type": "text", "source_expression": "`order`"}],
         "key": ["order"], "source": {"system": "SQL Server", "object": "dbo.t"}}
    assert "[order] AS [order]" in land.sql_text(c, quote="bracket")
    assert '"order" AS "order"' in land.sql_text(c, quote="double")
    _all(_flat(_read(REFS / "excel-power-query.md")), ["`[order] AS [order]`", "`\"order\" AS \"order\"`"],
         "excel-power-query.md")


def test_helper_patterns_state_the_split_helper_guard_and_no_split_spec() -> None:
    """Requests v4_P7 R1 and R3: the helper is IFNA only when every guard around the part is IFNA; split --spec is
    refused in this version (split.py), so the reference does not offer it."""
    text = _flat(_read(REFS / "helper-patterns.md"))
    _all(text, ["only when every IFERROR or IFNA around the part is IFNA", "`rotm.py split --spec` is refused"],
         "helper-patterns.md")
    assert "--spec <model.spec.json>" not in text

"""Tests of the v5 documents (package P8 of the v5 round, 2026-11-25). Owner: P8.

- [A-aera-19, C-sol-12] (1): references/fast-standard-rules.md, rows 2.01-04 and 2.01-05, states FAST's rule and,
  apart from it, what the plugin does; each statement is compared with the built worked example.
- [A-aera-19, C-sol-12] (2): google-sheets.md and excel-power-query.md end with a section "Sources" that has a row
  for every research identifier the file cites.
- [J-3]: SKILL.md and README.md "What is and is not verified": the three v5 items of the verified list with their
  tests (plan section 5.4). v6 (J-3): the subsection "Runs on the final v5 files" became "Runs on the final v6
  files"; v7 (J-6): "Runs on the final v7 files"; v8 (J-2): "Runs on the final v8 files"; v9 (J-2): "Runs on the
  final v9 files"; test_v9_docs.py checks it, the v9 items and the integrator's sentences.
- The documents of the other packages' v5 changes (plan section 6.8): the unit rule and the valid range
  ([A-aera-1]), the ids ([J-1]), land --control ([A-aera-6, C-sol-3]), the query file and the attached query
  ([A-aera-2, C-sol-1, C-sol-4]), {blk:<block>.<item>} and the Difference of a date check ([A-aera-9, C-sol-14,
  J-2]), the font size of a sparse calculation sheet ([A-aera-15]), a reply that is only a question
  ([A-aera-4, C-sol-2]), a date and time with an offset ([A-aera-16, A-aera-17]), the words of the audit-on-write
  hook ([A-aera-18]) and the check recomputation ([A-aera-3, C-sol-5]).
- Tests whose names end in `_in_the_code` read a name or a text from the code of another package (plan I-7 and
  I-9): they fail until that package has written it.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v5_docs.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
EXAMPLE = SKILL / "examples" / "sales_vs_target"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")

PY = [sys.executable, "-P", "-X", "utf8", "-B"]

RUNS_V5 = "### Runs on the final v5 files"
P_RUNS_V5 = "Integrator: replace this line with the runs on the final v5 files."
# v6 (J-3): the subsection that replaced RUNS_V5; v7 (J-6): the subsection that replaces it (test_v7_docs.py checks
# its contents).
RUNS_V6 = "### Runs on the final v6 files"
RUNS_V7 = "### Runs on the final v7 files"
# v8 (r7 verdict [J-2]): the subsection that replaced RUNS_V7.
RUNS_V8 = "### Runs on the final v8 files"
# v9 (r8 verdict [J-2]): the subsection that replaces RUNS_V8 (test_v9_docs.py checks its contents).
RUNS_V9 = "### Runs on the final v9 files"

# The three v5 items of the verified list and their tests (plan section 5.4).
V5_VERIFIED = {
    "Landing an export with a source total": [
        "test_v5_land.py::test_export_with_more_rows_and_a_new_total_lands_and_checks",
        "test_v5_land.py::test_export_with_fewer_rows_and_a_new_total_lands_and_checks",
        "test_v5_land.py::test_wrong_control_is_l_total_and_writes_nothing",
        "test_v5_land.py::test_later_build_takes_the_recorded_total"],
    "The check recomputation rule": [
        "test_v5_check.py::test_result_replaced_by_zero_is_c_recompute_in_check",
        "test_v5_check.py::test_result_replaced_by_zero_is_c_recompute_in_audit",
        "test_v5_check.py::test_shipped_models_give_no_c_recompute"],
    "The query-text comparison at refresh": [
        "test_v5_check.py::test_refresh_is_refused_when_the_query_file_differs_from_the_attached_text",
        "test_v5_check.py::test_refresh_refusal_for_a_changed_query_file_without_excel"],
}

# Numbers and words of the v4 runs that v5 must not carry (J-3).
V4_RUN_TEXTS = ("2,391", "2,085", "19:32 to 19:41", "19:28 to 20:13", "19:41 to 19:52", "19:41 to 19:48",
                "19:14 to 19:16", "19:11 to 19:13", "19:17 KST", "Version v4", "on the final v4 files",
                "for v4 only")

UNIT_RULE = "An input that converts a unit must apply to both sides of every comparison, or be fixed"
NOT_VERIFIED_STATE = "(the audit read the saved values; recalculation was not verified by this hook)"


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


def _ref(name: str) -> str:
    return _flat(_read(REFS / name))


def _command_skill(name: str) -> str:
    return _flat(_read(PLUGIN / "skills" / name / "SKILL.md"))


def _verified_files() -> list[Path]:
    return [SKILL / "SKILL.md"] + ([PLUGIN / "README.md"] if HAS_PLUGIN else [])


def _verified(path: Path) -> str:
    return _section(_read(path), "## What is and is not verified")


def _hooks_row(path: Path, hook: str, next_hook: str | None) -> str:
    heading = "## Hooks" + (" (when the plugin is installed)" if path.name == "SKILL.md" else "")
    hooks = _flat(_section(_read(path), heading))
    row = hooks.split(f"| {hook} |", 1)[1]
    return row.split(f"| {next_hook} |", 1)[0] if next_hook else row.split("\n", 1)[0]


def _help(*args: str) -> str:
    proc = subprocess.run(PY + [str(SCRIPTS / "rotm.py"), *args, "-h"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    return proc.stdout


# ----------------------------------------------------------------------------- [A-aera-19, C-sol-12] (1) FAST rows

def _fast_row() -> list[str]:
    for line in _read(REFS / "fast-standard-rules.md").splitlines():
        if line.startswith("| 2.01-04, 2.01-05 |"):
            return [c.strip() for c in line.strip().strip("|").split(" | ")]
    raise AssertionError("fast-standard-rules.md: no row '2.01-04, 2.01-05'")


def test_fast_rows_2_01_04_and_05_state_fast_and_the_plugin_apart() -> None:
    rule, says, evidence, plugin = _fast_row()
    assert "top to bottom and left to right" in says and "grey shading" in says, says
    assert "platforms C1.18" in evidence and "platforms C1.53, confirmed" in evidence, evidence
    _all(plugin, ["intended exception", "`checks!C2`", "below it and to its right", "FAST 2.03-05",
                  "`=checks!$C$2`", "F2F2F2", "`style-dictionary.md`", "`calc!C6`", "the unit column",
                  '"light shade gray for the column headers and slightly less shade gray for row headers" '
                  "(Max, spec 1.2)"], "fast-standard-rules.md row 2.01-04, 2.01-05")
    text = _read(REFS / "fast-standard-rules.md")
    assert "writes no counter-flow" not in text and "Grey means headers only" not in text


def _formula_of(cell) -> str:
    v = cell.value
    return v if isinstance(v, str) else getattr(v, "text", "")


def _same_sheet_counter_flow(ws) -> list[str]:
    """Cells whose formula reads a cell of its own sheet below it or to its right."""
    from openpyxl.utils import column_index_from_string
    ref = re.compile(r"(?<![A-Za-z_$!.\d])\$?([A-Z]{1,3})\$?(\d+)(?![\d(A-Za-z_])")
    other_sheet = re.compile(r"(?:'[^']+'|[A-Za-z_][\w.]*)!\$?[A-Z]{1,3}\$?\d*(?::\$?[A-Z]{1,3}\$?\d*)?")
    hits = []
    for row in ws.iter_rows():
        for c in row:
            f = _formula_of(c)
            if not f.startswith("="):
                continue
            text = other_sheet.sub("", re.sub(r'"[^"]*"', "", f))
            for m in ref.finditer(text):
                col, r = column_index_from_string(m.group(1)), int(m.group(2))
                if r > c.row or (r == c.row and col > c.column):
                    hits.append(f"{ws.title}!{c.coordinate}")
                    break
    return hits


def test_fast_row_statements_hold_in_the_built_worked_example() -> None:
    """Each statement of the plugin's column, read in the built worked example (examples/sales_vs_target)."""
    import openpyxl
    wb = openpyxl.load_workbook(EXAMPLE / "model.xlsx")
    _rule, _says, _evidence, plugin = _fast_row()
    master = _formula_of(wb["checks"]["C2"])
    assert f"`{master}`" in plugin, f"the row quotes another master check than checks!C2 holds: {master}"
    m = re.search(r"SUM\(I(\d+):I(\d+)\)", master)
    assert m and int(m.group(1)) > 2, master                          # results below it (rows 5 on) and to its right (I)
    hits = [h for ws in wb.worksheets for h in _same_sheet_counter_flow(ws)]
    assert hits == ["checks!C2"], hits
    for name in ("cover", "inputs", "calc", "summary", "notes"):
        assert _formula_of(wb[name]["C2"]) == "=checks!$C$2", name
    unit = wb["calc"]["C6"]
    assert unit.fill.fill_type == "solid" and unit.fill.fgColor.rgb.endswith("F2F2F2"), unit.fill.fgColor.rgb
    assert wb["calc"]["C5"].value == "Unit"
    assert "the unit column F2F2F2" in _read(REFS / "fast-standard-rules.md")
    dictionary = json.loads(_read(REFS / "style_dictionary.json"))
    assert json.dumps(dictionary).count("row_header_fill") >= 2
    assert "| Grey on the unit column | F2F2F2" in _read(REFS / "style-dictionary.md")


# ----------------------------------------------------------------------------- [A-aera-19, C-sol-12] (2) Sources

CITE = re.compile(r"(platforms|researcher 2|books|local guides|research synthesis)\s+"
                  r"((?:(?:#\d+|section \d+|[A-Z]{1,4}-\d+|[A-Z]\d+(?:\.\d+)?)(?:\s*(?:,|and|to)\s*)?)+)")
ONE_ID = re.compile(r"#\d+|section \d+|[A-Z]{1,4}-\d+|[A-Z]\d+(?:\.\d+)?")


def _expand(ids_text: str) -> list[str]:
    """The ids of one citation, with 'X to Y' of the same group written out (B2.4 to B2.6 -> B2.4, B2.5, B2.6)."""
    tokens = re.findall(r"#\d+|section \d+|[A-Z]{1,4}-\d+|[A-Z]\d+(?:\.\d+)?|\bto\b", ids_text)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "to" and out and i + 1 < len(tokens):
            first, last = out[-1], tokens[i + 1]
            a, b = re.fullmatch(r"([A-Z]\d+\.)(\d+)", first), re.fullmatch(r"([A-Z]\d+\.)(\d+)", last)
            if a and b and a.group(1) == b.group(1):
                out += [f"{a.group(1)}{n}" for n in range(int(a.group(2)) + 1, int(b.group(2)) + 1)]
            else:
                out.append(last)
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def _cited(text: str) -> set[str]:
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    body = re.sub(r"## Evidence labels\n.*?(?=\n## )", "", body, flags=re.S)
    body = body.split("\n## Sources\n", 1)[0]
    found: set[str] = set()
    for m in CITE.finditer(body):
        found |= {f"{m.group(1)} {i}" for i in _expand(m.group(2))}
    return found


def _source_rows(text: str) -> dict[str, list[str]]:
    assert "\n## Sources\n" in text, "no section 'Sources'"
    section = text.split("\n## Sources\n", 1)[1]
    assert "\n## " not in section, "'Sources' must be the last section"
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        if line.startswith("| ") and not line.startswith("| Identifier") and not line.startswith("| ---"):
            cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
            assert len(cells) == 5, f"a row needs identifier, publisher, page title, link and date: {line}"
            assert cells[0] not in rows, f"two rows for {cells[0]}"
            rows[cells[0]] = cells
    return rows


@pytest.mark.parametrize("name", ["google-sheets.md", "excel-power-query.md"])
def test_every_cited_identifier_has_a_source_row(name: str) -> None:
    text = _read(REFS / name)
    cited = _cited(text)
    assert len(cited) >= 25, sorted(cited)
    rows = _source_rows(text)
    missing = sorted(cited - set(rows))
    assert not missing, f"{name}: cited identifiers without a row in 'Sources': {missing}"
    extra = sorted(set(rows) - cited)
    assert not extra, f"{name}: rows in 'Sources' for identifiers the file does not cite: {extra}"


@pytest.mark.parametrize("name", ["google-sheets.md", "excel-power-query.md"])
def test_every_source_row_has_a_publisher_a_title_a_link_and_a_date(name: str) -> None:
    rows = _source_rows(_read(REFS / name))
    for ident, (_i, publisher, title, link, read) in rows.items():
        assert publisher and title and link and read, ident
        links = [u.strip() for u in link.split(" ; ")]
        if all(u.startswith("https://") for u in links):
            assert publisher != "-" and "no public page" not in title, ident
            assert len(links) == len([t for t in title.split("; ") if t]), f"{ident}: one title per link"
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", read), ident
        else:
            assert "no public page" in title + link, f"{ident}: no link and not marked as having no public page"
    if name == "excel-power-query.md":
        for ident in ("platforms B2.4", "platforms B2.5", "platforms B2.6"):
            assert rows[ident][2].startswith("local test"), f"{ident} is a local Excel observation"
    assert "Evidence labels" in _section(_read(REFS / name), "## Evidence labels") \
        and "[Sources](#sources)" in _read(REFS / name)


# ----------------------------------------------------------------------------- [J-3] what is and is not verified

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_the_v5_runs_subsection_is_replaced_by_the_v6_one(path: Path) -> None:
    """v6 (J-3): the runs on the final v5 files are not carried over. v7 (J-6): the v6 subsection was replaced by
    the v7 one in turn; test_v7_docs.py checks it (its placeholder or the integrator's dated runs, the version line
    and the v6 run numbers left out). v8 (r7 verdict [J-2]): the v7 subsection was replaced by the v8 one; v9 (r8
    verdict [J-2]): the v8 subsection was replaced by the v9 one; test_v9_docs.py checks it."""
    section = _verified(path)
    assert RUNS_V5 not in section, f"{path.name}: the v5 subsection is left"
    assert "### Runs on the final v4 files" not in section and "### Runs on the final v3 files" not in section
    assert RUNS_V6 not in section and RUNS_V7 not in section and RUNS_V8 not in section and RUNS_V9 in section
    assert P_RUNS_V5 not in section and "Version v5 of" not in section


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_no_v4_run_statement_is_carried_over(path: Path) -> None:
    text = _flat(_verified(path))
    found = [t for t in V4_RUN_TEXTS if t in text]
    assert not found, f"{path.name}: v4 run statements left: {found}"


@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_verified_list_names_the_v5_items_with_their_tests(path: Path) -> None:
    """v6 (J-3): the three v5 items stay in the verified list with their tests (the tests did not change); the
    integrator's sentence and the v6 items are checked in test_v6_docs.py."""
    section = _verified(path)
    verified = _flat(_section(section, "### Verified, with the tests that show it"))
    not_verified = _flat(_section(section, "### Not verified"))
    present = [item for item in V5_VERIFIED if item in verified]
    assert present == list(V5_VERIFIED), f"{path.name}: v5 items missing: {set(V5_VERIFIED) - set(present)}"
    for item in present:
        missing = [t for t in V5_VERIFIED[item] if t not in verified]
        assert not missing, f"{path.name}: the verified item '{item}' does not name {missing}"
    assert "Google" not in verified, f"{path.name}: a Google Sheets statement in the verified list"
    assert "Google Sheets" in not_verified and "--control" in not_verified


# ----------------------------------------------------------------------------- [A-aera-1] the unit rule, the valid range

def test_unit_rule_in_common_mistakes_and_in_the_specification_format() -> None:
    mistakes = _flat(_section(_skill_md(), "## Common mistakes"))
    _all(mistakes, [UNIT_RULE, '"valid": {"min": 1000, "max": 1000}', "outside its own `valid` range"],
         "SKILL.md Common mistakes")
    spec = _ref("model-spec-format.md")
    inputs = _flat(_section(_read(REFS / "model-spec-format.md"), "## Inputs"))
    _all(inputs, [UNIT_RULE, "outside its valid range <min> to <max>", "which is not one of its valid values",
                  "write a value from <min> to <max>", "<input path>.values[<r>][<c>]", "exit 2"],
         "model-spec-format.md Inputs")
    assert '"valid": {"min": 1, "max": 1000000}' not in spec, "the example converts one side of a comparison"
    assert spec.count('"valid": {"min": 1000, "max": 1000}') >= 3


# ----------------------------------------------------------------------------- [J-1] the ids

def test_ids_section_states_the_shared_set_and_the_refusal() -> None:
    ids = _flat(_section(_read(REFS / "model-spec-format.md"), "## Ids"))
    _all(ids, ["Inputs, blocks and checks share one set of ids", "`check_tolerance`", "`<t>_rows`", "`<t>_cover`",
               "`<t>_beyond`", "`<t>_keys`", "`<t>_total_<column>`", "`<t>_nulls_<column>`",
               "id '<id>' is used by <kind A> at <path A> and by <kind B> at <path B>", "exit 2",
               "A key output", "a helper column", "a sheet name", "may have the same id"],
         "model-spec-format.md Ids")


def test_ids_section_quotes_the_refusal_fix_in_the_code() -> None:
    from lib import spec
    ids = _flat(_section(_read(REFS / "model-spec-format.md"), "## Ids"))
    assert spec.ID_SET_FIX in ids, spec.ID_SET_FIX
    for gid in spec.GENERATED_INPUTS:
        assert f"`{gid}`" in ids, gid
    for suffix in spec.GENERATED_CHECK_SUFFIXES:
        assert f"`<t>_{suffix}`" in ids, suffix
    source = _read(SCRIPTS / "lib" / "spec.py")
    assert "is used by {kind_a} at {path_a} and by {what} at {path}" in source


# ----------------------------------------------------------------------------- [A-aera-6, C-sol-3] land --control

STEP_4_WORDS = ["--force --control <t>.<column>=<source total>", "control input cell", "before the download",
                "`<t>: control total of <column>`", "D-CONTROL-INPUT", "the cell was not updated", "H-COVERAGE",
                "H-BEYOND", "What a refresh does to the formulas that read an extract", "L-TOTAL",
                "raw/<t>.controls.json", "not tested in Google Sheets", "tested here with a local stand-in",
                "check <copy of the model folder> --strict",
                "test_v5_land.py::test_export_with_more_rows_and_a_new_total_lands_and_checks",
                "test_v5_land.py::test_export_with_fewer_rows_and_a_new_total_lands_and_checks"]


def test_land_control_is_named_where_a_new_extract_is_landed() -> None:
    _all(_step(2), ["--force --control <t>.<column>=<source total>", "raw/<t>.controls.json", "L-TOTAL"],
         "SKILL.md P2")
    _all(_step(8), ["--control <t>.<column>=<source total>"], "SKILL.md step 8")
    later = _flat(_section(_read(REFS / "column-contract.md"), "## What the plugin does with the contract later"))
    _all(later, ["--force --control <t>.<column>=<source total>", "L-TOTAL", "raw/<t>.controls.json",
                 "`--control` needs `--csv`"], "column-contract.md, later")
    _all(_ref("excel-power-query.md"), ["--csv <file> --force --control <t>.<column>=<source total>"],
         "excel-power-query.md, X-NO-QUERY")
    if HAS_PLUGIN:
        _all(_command_skill("check"), ["--force --control <t>.<column>=<source total>"], "skills/check/SKILL.md")


def test_land_control_in_the_code() -> None:
    """I-9: the option the documents name is an option of land (package P4)."""
    assert "--control" in _help("land"), "rotm.py land has no --control yet"


def test_google_sheets_step_4_has_no_dead_end_sentence() -> None:
    text = _read(REFS / "google-sheets.md")
    assert "The existing commands do not accept a refreshed static table with a new source total." not in text
    assert "takes no control total of its own" not in text
    _all(_flat(_section(text, "## Models kept in Google Sheets")), STEP_4_WORDS, "google-sheets.md step 4")


# ----------------------------------------------------------------------------- [A-aera-2, C-sol-1, C-sol-4] the query

def test_power_query_reference_states_the_query_file_and_the_attached_query() -> None:
    text = _ref("excel-power-query.md")
    _all(text, ["`land` and `land --keep-queries` change the query file only; the workbook keeps the query it was "
                "given until it is attached again", "query_body_sha256",
                "differs from the query text attached to the workbook", "nothing refreshed",
                "the manifest records that a Power Query query loads it, but the workbook holds no query table at A1 "
                "of raw_<t>; it was not refreshed and nothing was accepted for it",
                "B-REFUSED", "the query cannot be attached again", "nothing was written", "W-PQ-NOT-ATTACHED"],
         "excel-power-query.md")


@needs_plugin
def test_check_skill_states_the_refusal_and_x_no_query_for_one_table() -> None:
    _all(_command_skill("check"), ["differs from the query text attached to the workbook",
                                   "ROTM land MODEL --table <t> --power-query", "change the query file only",
                                   "X-NO-QUERY` for that table"], "skills/check/SKILL.md")
    _all(_command_skill("build"), ["B-REFUSED", "before anything is written", "W-PQ-NOT-ATTACHED"],
         "skills/build/SKILL.md")


def test_skill_md_step_8_states_the_refusal_and_x_no_query_for_one_table() -> None:
    _all(_step(8), ["differs from the query text attached to the workbook", "change the query file only",
                    "`X-NO-QUERY` for that table"], "SKILL.md step 8")


def test_x_no_query_text_in_the_code() -> None:
    """The X-NO-QUERY message the documents quote is the one check.py prints (package P3)."""
    source = _flat(_read(SCRIPTS / "lib" / "check.py"))
    assert "the manifest records that a Power Query query loads it, but the workbook " in source
    assert "holds no query table at A1 of {sheet}; it was not refreshed and nothing was accepted for it" in source


# ----------------------------------------------------------------------------- [A-aera-9, C-sol-14, J-2]

def test_blk_item_placeholder_and_the_date_difference() -> None:
    text = _read(REFS / "model-spec-format.md")
    placeholders = _flat(_section(text, "## Placeholders"))
    _all(placeholders, ["| `{blk:<block>.<item>}` | every expression |", "follows the item when the items are "
                        "reordered", "needs no row number", "unknown item '<item>' of block '<block>' in",
                        "SUMPRODUCT({blk:bridge.var_pct},{blk:bridge.tgt})",
                        "SUMPRODUCT(--({blk:bridge.tgt}<>0),{blk:bridge.var})"], "model-spec-format.md Placeholders")
    checks = _flat(_section(text, "## Checks"))
    _all(checks, ["A check of dates", "Difference cell (G) shows a count of days", "(format `count`)",
                  "`amount2`", "the tolerance cell stays General"], "model-spec-format.md Checks")


def test_blk_item_example_is_the_worked_examples_check() -> None:
    spec = json.loads(_read(EXAMPLE / "model.spec.json"))
    checks = [c for s in spec["sheets"] if s.get("type") == "checks" for c in s.get("checks", [])]
    var = next((c for c in checks if c["id"] == "var_pct_all"), None)
    assert var is not None, "the worked example has no check var_pct_all"
    placeholders = _flat(_section(_read(REFS / "model-spec-format.md"), "## Placeholders"))
    assert f"actual `{var['actual']}`" in placeholders and f"expected `{var['expected']}`" in placeholders, var
    assert not any(re.search(r"\b1\+1\b", c.get(k, "")) for c in checks for k in ("actual", "expected"))


# ----------------------------------------------------------------------------- [A-aera-15] the font size

def test_font_size_of_a_sparse_calculation_sheet_is_documented() -> None:
    words = ['"font_size": 9', "white space", "size 8 is for dense sheets", "I-SPARSE-SHEET",
             "rules.sparse_sheet_cells", "100"]
    _all(_step(6), words, "SKILL.md P6")
    _all(_ref("model-spec-format.md"), words + ["does not change the strict exit code",
                                                "takes size 8 from its role (calc)"], "model-spec-format.md")
    if HAS_PLUGIN:
        choices = _flat(_section(_read(PLUGIN / "README.md"), "## Choices left to Max"))
        _all(choices, ["| 17 | font size of a sheet |", "I-SPARSE-SHEET", "measured density (not built)"],
             "README.md Choices left to Max")


def test_sparse_sheet_threshold_in_the_code() -> None:
    """The 100 cells the documents state is the dictionary's rule (package P2)."""
    rules = json.loads(_read(REFS / "style_dictionary.json")).get("rules", {})
    assert "sparse_sheet_cells" in rules, "style_dictionary.json has no rules.sparse_sheet_cells yet"
    value = rules["sparse_sheet_cells"]
    assert (value.get("value") if isinstance(value, dict) else value) == 100


# ----------------------------------------------------------------------------- [A-aera-4, C-sol-2] the agreement guard

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_hooks_tables_refuse_a_question_and_read_other_tables_apart(path: Path) -> None:
    row = _hooks_row(path, "contract gate", "raw-sheet guard")
    _all(row, ["only a question", "answer the question and wait for the user's reply",
               "another table than the one given with `--table` does not count against this table"],
         f"{path.name} hooks table, contract gate")


def test_contract_skill_and_p1_refuse_a_reply_that_is_only_a_question() -> None:
    _all(_step(1), ["only a question", "is a question", "Answer the question and wait for the user's reply",
                    "Looks good, can you go ahead?"], "SKILL.md P1 step 6")
    if HAS_PLUGIN:
        _all(_command_skill("contract"), ["only a question", "is a question", "Looks good, can you go ahead?",
                                          "does not count against this table", "I have not agreed. Please leave it "
                                          "proposed."], "skills/contract/SKILL.md")


def test_question_reason_in_the_code() -> None:
    """The reason the documents quote is contract.QUESTION_REASON (package P4)."""
    from lib import contract
    assert getattr(contract, "QUESTION_REASON", None) == "is a question"


# ----------------------------------------------------------------------------- [A-aera-16, A-aera-17] date and time

def test_column_contract_states_a_date_and_time_with_an_offset() -> None:
    text = _ref("column-contract.md")
    _all(text, ["`+09:00`", "is proposed as datetime", "lands it with the offset dropped", "L-PRECISION",
                "`2026-01-05T09:30:00+09:00`", "read a date and time with the same rule"], "column-contract.md")
    _all(_ref("model-spec-format.md"), ["with a `T` between date and time", "`+09:00`"], "model-spec-format.md")


# ----------------------------------------------------------------------------- [A-aera-18] the audit-on-write words

@pytest.mark.parametrize("path", _verified_files(), ids=lambda p: p.name)
def test_hooks_tables_give_the_audit_on_write_words(path: Path) -> None:
    row = _hooks_row(path, "audit on write", "finish check")
    _all(row, ["`(recalculated)`", f"`{NOT_VERIFIED_STATE}`", "reports/status.json", "sha256"],
         f"{path.name} hooks table, audit on write")


@needs_plugin
def test_audit_on_write_words_in_the_code() -> None:
    """The words the hooks tables quote are the hook's (package P5)."""
    source = _read(PLUGIN / "hooks" / "audit_on_write.py")
    assert NOT_VERIFIED_STATE.strip("()") in source


# ----------------------------------------------------------------------------- [A-aera-3, C-sol-5] the recomputation

def test_skill_md_states_the_check_recomputation() -> None:
    _all(_step(7), ["C-RECOMPUTE", "saved values of its own operand cells", "the master check again",
                    "the formula build wrote", "`references/checks.md`", "ROTM build MODEL --force"], "SKILL.md P7")


def test_c_recompute_in_the_code() -> None:
    """The rule id SKILL.md names is one the plugin prints (packages P3 and P5)."""
    texts = [_read(p) for p in (SCRIPTS / "lib").glob("*.py")]
    assert any('"C-RECOMPUTE"' in t for t in texts), "no module prints C-RECOMPUTE yet"

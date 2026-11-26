"""Tests of the v7 check changes and the P4 references (package P4 of the v7 plan). Owner: P4.

Entries:
- [A-aera-2, C-sol-5]: check --accept-refresh given without --refresh reads the query formula back through Excel and
  compares it with the manifest's query_formula_sha256, as check --refresh does: a difference is X-QUERY-CHANGED (an
  error), nothing is accepted, and raw/<t>.csv, raw/<t>.controls.json and the manifest stay byte for byte. Without
  Excel, or for a manifest without the hash, the acceptance prints a note that the query was not compared. The
  documents give every refresh command with --strict and say that without --strict a warning leaves exit 0.
- [A-aera-8]: a workbook that holds a function only Google Sheets has (functions.sheets_only_names(): Excel "no",
  Sheets "yes") is not opened in Excel by check: X-SHEETS-ONLY (information), the note "audited, not recalculated:
  functions only Google Sheets has (X-SHEETS-ONLY)", exit 3 when nothing else fails, the workbook byte for byte;
  --refresh and --accept-refresh accept nothing. The references say what is verified for such a model (T-21).
Supporting: T-18 (excel-power-query.md: a rebuild when the source of a live query cannot be reached) and T-19
([A-aera-11, A-aera-15] part (2): the two forms of the database M text and the systems for which land writes
[EnableFolding = true]).
Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from lib import build, check, common, excel, functions, land, manifest
import test_check as tc
from test_v4_check import _built_starter, _rows, _total, starter
from test_v6_check import (M_TEXT, SORTED_STEP, attached_starter, fake_excel, lines, out, report_of, rotm_attached,
                           rules, three_files, workbook_formula)

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
STARTER = SKILL / "templates" / "starter"
REFS = SKILL / "references"
EPQ = REFS / "excel-power-query.md"
CHECK_SKILL = PLUGIN / "skills" / "check" / "SKILL.md"

needs_plugin = pytest.mark.skipif(not CHECK_SKILL.is_file(), reason="the plugin folder around the skill is not here")


# ----------------------------------------------------------------------------- helpers

def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _plain(text: str) -> str:
    """Flattened, without code-span backticks."""
    return _flat(text.replace("`", ""))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text from `heading` to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading)
    rest = text[start + len(heading):]
    m = re.search(r"^#{1,%d} " % level, rest, flags=re.M)
    return heading + (rest[:m.start()] if m else rest)


def _reorder_raw_rows(root: Path) -> None:
    """Swap raw rows 2 and 3 of raw_lines in the workbook (as a refresh that returns the rows in another order)."""
    import openpyxl
    wb = root / "model.xlsx"
    book = openpyxl.load_workbook(wb)
    ws = book["raw_lines"]
    for col in range(1, ws.max_column + 1):
        a, b = ws.cell(2, col), ws.cell(3, col)
        a.value, b.value = b.value, a.value
    book.save(wb)


# ============================================================================= [A-aera-2, C-sol-5] code

@pytest.mark.excel
def test_standalone_acceptance_finds_a_query_changed_in_excel(tmp_path: Path) -> None:
    """The reviewer's case: the query changed through Excel without rotm.py and refreshed in Excel; then
    check --accept-refresh --strict without --refresh."""
    root = attached_starter(tmp_path)
    wb = root / "model.xlsx"
    formula = (workbook_formula(wb) or "").replace("\r\n", "\n")
    assert SORTED_STEP[0] in formula, formula[-300:]
    res = excel.pq_attach(wb, sheet="raw_lines", table_name="tbl_raw_lines", query_name="q_lines",
                          m_text=formula.replace(*SORTED_STEP))
    assert res.ok, res.message()
    before = three_files(root)
    report = tmp_path / "check.json"
    r = tc.run_cli("check", str(root), "--accept-refresh", "--strict", "--json", str(report))
    assert r.returncode != 0, out(r)
    rep = report_of(report)
    found = [f for f in rep["findings"] if f["rule"] == "X-QUERY-CHANGED"]
    assert len(found) == 1, [f["rule"] for f in rep["findings"]]
    f = found[0]
    assert f["severity"] == "error" and f["sheet"] == "raw_lines" and f["data"]["table"] == "lines"
    assert f["message"].startswith("raw_lines: the query q_lines that the workbook holds differs from the query "
                                   "rotm.py attached (manifest query_formula_sha256 sha256:"), f["message"]
    assert "nothing was accepted for raw_lines" in f["message"]
    assert not any(x["rule"] == "R-ACCEPTED" for x in rep["findings"])
    assert any(n.startswith("no table was accepted: a query the workbook holds differs from the query rotm.py "
                            "attached") for n in rep["notes"]), rep["notes"]
    assert rep["data"]["refreshed"] is False
    assert three_files(root) == before


@pytest.mark.excel
def test_standalone_acceptance_with_an_unchanged_query_exits_0(tmp_path: Path, monkeypatch) -> None:
    root = attached_starter(tmp_path)
    raw_csv = root / "raw" / "lines.csv"               # the file the attached query reads
    rows = list(csv.reader(raw_csv.open(encoding="utf-8", newline="")))
    rows[1][3] = str(int(rows[1][3]) + 1000)           # rows[0] is the header; the total stays the same
    rows[2][3] = str(int(rows[2][3]) - 1000)
    with raw_csv.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)
    report = tmp_path / "refresh.json"
    tc.run_cli("check", str(root), "--refresh", "--strict", "--json", str(report))   # refreshed in Excel
    assert report_of(report)["data"]["refreshed"] is True
    real = excel.inventory
    calls: list[Path] = []

    def counted(wb):
        calls.append(Path(wb))
        return real(wb)
    monkeypatch.setattr(excel, "inventory", counted)
    before = three_files(root)
    rep = check.check_model(root, strict=True, accept_refresh=True)
    assert rep.exit_code == 0, lines(rep)
    assert len(calls) == 1                              # the query formula was read back and compared
    assert "X-QUERY-CHANGED" not in rules(rep) and "R-ACCEPTED" in rules(rep), lines(rep)
    # the snapshot holds the refreshed rows (they equal the changed source file) and the manifest its new digest
    after = three_files(root)
    assert after["lines.csv"] == raw_csv.read_bytes()
    assert after["model.manifest.json"] != before["model.manifest.json"]


def test_standalone_acceptance_compares_the_query_formula(tmp_path: Path, monkeypatch) -> None:
    """Excel as available (fake): a changed formula stops the acceptance of changed rows; an equal one lets it
    through. The comparison runs for --accept-refresh without --refresh."""
    root = _built_starter(tmp_path)
    rotm_attached(root)
    _reorder_raw_rows(root)
    seen = fake_excel(monkeypatch, formulas={"q_lines": M_TEXT + "\n// changed in Excel"},
                      qtables={"raw_lines": "q_lines"})
    before = three_files(root)
    rep = check.check_model(root, accept_refresh=True)
    found = [f for f in rep.findings if f.rule == "X-QUERY-CHANGED"]
    assert len(found) == 1 and found[0].severity == "error" and rep.exit_code == 1, lines(rep)
    assert seen.inventories == 1
    assert not any(o.get("op") == "pq_refresh" for o in seen.jobs[0])
    assert "R-ACCEPTED" not in rules(rep), lines(rep)
    assert any(n.startswith("no table was accepted: a query the workbook holds differs") for n in rep.notes), \
        lines(rep)
    assert three_files(root) == before
    seen = fake_excel(monkeypatch, formulas={"q_lines": M_TEXT}, qtables={"raw_lines": "q_lines"})
    rep = check.check_model(root, accept_refresh=True)
    assert seen.inventories == 1 and "X-QUERY-CHANGED" not in rules(rep), lines(rep)
    assert "R-ACCEPTED" in rules(rep), lines(rep)
    assert three_files(root)["lines.csv"] != before["lines.csv"]


def test_standalone_acceptance_of_rows_already_landed_records_nothing_on_a_changed_query(tmp_path: Path,
                                                                                     monkeypatch) -> None:
    """Rows equal to the landed snapshot and a changed query: the manifest is not written (no workbook digest,
    no controls record) in that run."""
    root = _built_starter(tmp_path)
    rotm_attached(root)
    fake_excel(monkeypatch, formulas={"q_lines": M_TEXT + "\n// changed"}, qtables={"raw_lines": "q_lines"})
    man = manifest.load_manifest(root / "model.xlsx")
    manifest.raw_sheet(man, "lines").pop("workbook_digest", None)    # a check would record it
    manifest.write_manifest(root / "model.xlsx", man)
    before = three_files(root)
    total = _total(_rows(STARTER / "data" / "lines.csv"))      # the source total: an acceptance would record it
    rep = check.check_model(root, accept_refresh=True, controls={"lines.amount": float(total)},
                            control_texts=[f"lines.amount={total}"])
    assert "X-QUERY-CHANGED" in rules(rep), lines(rep)
    assert "D-TOTAL" not in rules(rep), lines(rep)
    assert three_files(root) == before


def test_standalone_acceptance_without_excel_notes_that_the_query_was_not_compared(tmp_path: Path,
                                                                                  monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _built_starter(tmp_path)
    rotm_attached(root)
    monkeypatch.setattr(check, "query_tables", lambda _wb: {"raw_lines": "q_lines"})
    rep = check.check_model(root, accept_refresh=True)
    note = next((n for n in rep.notes if n.startswith("--accept-refresh: the query of raw_lines was not read back")),
                None)
    assert note is not None, lines(rep)
    assert "Excel is not available" in note and "--refresh --strict" in note, note
    assert "X-QUERY-CHANGED" not in rules(rep)
    rep = check.check_model(root, excel=False, accept_refresh=True)
    note = next(n for n in rep.notes if n.startswith("--accept-refresh: the query of raw_lines was not read back"))
    assert "--no-excel was given" in note, note
    # a manifest without the hash: the note of v6, for the acceptance
    rotm_attached(root, None)
    rep = check.check_model(root, accept_refresh=True)
    note = next((n for n in rep.notes if "records no hash of the query formula" in n), None)
    assert note is not None, lines(rep)
    assert note.startswith("--accept-refresh: raw_lines's manifest records no hash of the query formula the workbook "
                           "holds (attached before v6), so the workbook's query was not compared with the attached "
                           "text; "), note
    assert not any(n.startswith("--accept-refresh: the query of raw_lines was not read back") for n in rep.notes)


def test_check_without_acceptance_prints_no_acceptance_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _built_starter(tmp_path)
    rotm_attached(root)
    monkeypatch.setattr(check, "query_tables", lambda _wb: {"raw_lines": "q_lines"})
    rep = check.check_model(root)
    assert not any(n.startswith("--accept-refresh:") for n in rep.notes), lines(rep)


def test_x_query_changed_names_both_options_in_its_rule_text() -> None:
    sev, text = check.RULES["X-QUERY-CHANGED"]
    assert sev == "warning" and text.startswith("--refresh or --accept-refresh (with Excel): ")


# ============================================================================= [A-aera-2, C-sol-5] documents

_COMMAND = re.compile(r"(?:rotm\.py|ROTM) check (?:\"<model>\"|MODEL|<model>)")
_REFRESH = re.compile(r"(?<![-\w])--refresh(?![-\w])")


def _refresh_commands(text: str) -> list[str]:
    """The check commands with --refresh that a document gives to run: code spans and lines of fenced code blocks
    that hold `rotm.py check` (or `ROTM check`) with a model folder argument and the flag --refresh. A name of the
    mode without a model folder, such as `check --refresh`, is not a command."""
    blocks = re.findall(r"^```[^\n]*\n(.*?)^```", text, flags=re.M | re.S)
    outside = re.sub(r"^```[^\n]*\n.*?^```", "", text, flags=re.M | re.S)
    items = re.findall(r"`([^`\n]+)`", outside) + [ln for b in blocks for ln in b.splitlines()]
    return [s for s in items if _COMMAND.search(s) and _REFRESH.search(s)]


STRICT_SENTENCE = ("Give --strict: without it a warning such as X-NO-QUERY leaves exit 0, so read the report's "
                   "refreshed value (data.refreshed in")
T4_TEXTS = [
    "With Excel, check --accept-refresh reads the query back and compares it too, also when it is given without "
    "--refresh: on a difference it reports X-QUERY-CHANGED as an error, accepts nothing, and raw/<t>.csv, "
    "raw/<t>.controls.json and the manifest stay byte for byte.",
    "Without Excel, or for a manifest without the hash, the acceptance prints the note that the query was not "
    "compared.",
    "A value typed into a raw sheet that a query loads is not found by an acceptance alone: run check --refresh "
    "first, which loads the rows from the source again.",
    "X-NO-QUERY stays a warning (an error with --accept-refresh).",
]


@pytest.mark.parametrize("path", [EPQ, CHECK_SKILL], ids=["excel-power-query.md", "skills/check/SKILL.md"])
def test_every_refresh_command_holds_strict(path: Path) -> None:
    if not path.is_file():
        pytest.skip(f"{path.name} is not here")
    cmds = _refresh_commands(_read(path))
    assert len(cmds) >= 3, cmds
    missing = [c for c in cmds if "--strict" not in c]
    assert not missing, f"{path.name}: refresh commands without --strict: {missing}"


@pytest.mark.parametrize("path", [EPQ, CHECK_SKILL], ids=["excel-power-query.md", "skills/check/SKILL.md"])
def test_the_documents_say_that_without_strict_a_warning_leaves_exit_0(path: Path) -> None:
    if not path.is_file():
        pytest.skip(f"{path.name} is not here")
    text = _plain(_read(path))
    assert STRICT_SENTENCE in text, path.name
    missing = [t for t in T4_TEXTS if t not in text]
    assert not missing, f"{path.name}: missing {missing}"
    assert "check --accept-refresh alone does not read the query again" not in text, path.name


def test_the_power_query_reference_names_the_typed_value_case_at_the_manual_refresh() -> None:
    text = _plain(_read(EPQ))
    manual = text[text.index("A refresh by hand does not validate the new rows"):]
    manual = manual[:manual.index("If the formats were changed by hand")]
    assert "A value typed into a raw sheet that a query loads is not found by an acceptance alone" in manual, manual


def test_the_refresh_commands_test_finds_a_command_without_strict() -> None:
    """The rule of _refresh_commands on a sample text, so that a later edit that drops --strict is found."""
    sample = ("Run `rotm.py check \"<model>\" --refresh` then `check --refresh` and `ROTM check MODEL --refresh --strict`"
              ".\n```\nrotm.py check \"<model>\" --refresh\n```\n`rotm.py check \"<model>\" --accept-refresh`")
    cmds = _refresh_commands(sample)
    assert cmds == ['rotm.py check "<model>" --refresh', "ROTM check MODEL --refresh --strict",
                    'rotm.py check "<model>" --refresh'], cmds


# ============================================================================= [A-aera-8] code

JOIN_EXPR = 'JOIN("-",{this:line_id},{this:category})'
AMP_EXPR = '{this:line_id}&"-"&{this:category}'


def _sheets_model(tmp_path: Path, expr: str) -> Path:
    """A copy of the starter with product sheets and one more helper column `line_key` = `expr`, built."""
    def edit(spec: dict) -> None:
        spec["product"] = "sheets"
        helpers = next(s for s in spec["sheets"] if s["type"] == "helpers")
        helpers["columns"].append({"id": "line_key", "label": "Line key", "expr": expr, "format": "text"})
    root = starter(tmp_path / "m", spec_edit=edit)
    rep = build.build(root / "model.spec.json", power_query=False)
    assert rep.counts()["errors"] == 0, lines(rep)
    return root


def _line_key_col(root: Path) -> str:
    man = json.loads((root / "model.manifest.json").read_text(encoding="utf-8"))
    helpers = next(e for e in man["sheets"] if e.get("type") == "helpers")
    return next(c["col"] for c in helpers["columns"] if c.get("id") == "line_key")


@pytest.mark.excel
def test_check_of_a_model_with_a_function_only_google_sheets_has_is_not_recalculated(tmp_path: Path) -> None:
    """The reviewer's case: product sheets, JOIN in a helper column; Excel is available."""
    root = _sheets_model(tmp_path, JOIN_EXPR)
    wb = root / "model.xlsx"
    sha = common.file_sha256(wb)
    before = three_files(root)
    report = tmp_path / "check.json"
    r = tc.run_cli("check", str(root), "--strict", "--json", str(report))
    assert r.returncode == 3, out(r)
    rep = report_of(report)
    found = [f for f in rep["findings"] if f["rule"] == "X-SHEETS-ONLY"]
    assert len(found) == 1 and found[0]["severity"] == "info", rep["findings"]
    col = _line_key_col(root)
    assert found[0]["message"] == (f"the workbook holds functions only Google Sheets has: JOIN (60 cell(s), first "
                                   f"h_lines!{col}2); Excel cannot calculate them, so the workbook was not opened in "
                                   "Excel and not recalculated"), found[0]["message"]
    assert found[0]["sheet"] == "h_lines" and found[0]["cell"] == f"{col}2"
    assert "for JOIN the & operator" in found[0]["fix"], found[0]["fix"]
    assert any(n.startswith("audited, not recalculated: functions only Google Sheets has (X-SHEETS-ONLY). Raw sheets, "
                            "contracts and coverage were checked by code") for n in rep["notes"]), rep["notes"]
    assert "not recalculated" in r.stdout
    assert rep["summary"]["errors"] == 0 and rep["summary"]["warnings"] == 0, rep["findings"]
    assert rep["data"]["recalculated"] is False and rep["data"]["excel_pid"] is None
    assert common.file_sha256(wb) == sha
    assert three_files(root)["lines.csv"] == before["lines.csv"]


@pytest.mark.excel
def test_the_same_model_written_with_the_ampersand_passes_check(tmp_path: Path) -> None:
    root = _sheets_model(tmp_path, AMP_EXPR)
    r = tc.run_cli("check", str(root), "--strict")
    assert r.returncode == 0, out(r)
    assert "X-SHEETS-ONLY" not in r.stdout


def test_a_function_only_google_sheets_has_starts_no_excel_job(tmp_path: Path, monkeypatch) -> None:
    """Excel as available (fake): no job, no inventory; --refresh and --accept-refresh accept nothing."""
    root = _sheets_model(tmp_path, JOIN_EXPR)
    wb = root / "model.xlsx"
    sha = common.file_sha256(wb)
    seen = fake_excel(monkeypatch, formulas=AssertionError("the formula was read"))
    rep = check.check_model(root, strict=True)
    assert seen.jobs == [] and seen.inventories == 0
    assert rep.exit_code == 3 and rules(rep) == ["X-SHEETS-ONLY"], lines(rep)
    assert rep.data["recalculated"] is False and rep.data["excel_pid"] is None
    _reorder_raw_rows(root)                             # changed rows that an acceptance would record
    sha = common.file_sha256(wb)
    before = three_files(root)
    for kw in ({"accept_refresh": True}, {"refresh": True, "accept_refresh": True}):
        rep = check.check_model(root, **kw)
        assert seen.jobs == [] and seen.inventories == 0
        assert "R-ACCEPTED" not in rules(rep), lines(rep)
        assert any(n.startswith("no table was accepted") and "X-SHEETS-ONLY" in n for n in rep.notes), lines(rep)
        assert three_files(root) == before and common.file_sha256(wb) == sha


def test_the_finding_names_each_function_with_its_cells_and_replacement(tmp_path: Path) -> None:
    import openpyxl
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "calc"
    ws["A1"], ws["B1"] = "a", "b"
    ws["C1"] = '=JOIN("-",A1,B1)'
    ws["C2"] = '=JOIN("-",A1,B1)'
    ws["D1"] = '="JOIN(" & A1 & ")"'                   # the name inside a text literal: no call
    ws["D2"] = "=JOIN text"                             # stored as text, not a formula
    ws["D2"].data_type = "s"
    second = book.create_sheet("notes")
    second["B3"] = '=IF(REGEXMATCH(A1,"x"),1,0)'
    path = tmp_path / "w.xlsx"
    book.save(path)
    found = check.sheets_only_cells(path)
    assert found == {"JOIN": {"count": 2, "first": "calc!C1"}, "REGEXMATCH": {"count": 1, "first": "notes!B3"}}, found
    f = check.sheets_only_finding(found)
    assert f.rule == "X-SHEETS-ONLY" and f.severity == "info" and f.sheet == "calc" and f.cell == "C1"
    assert f.message == ("the workbook holds functions only Google Sheets has: JOIN (2 cell(s), first calc!C1); "
                         "REGEXMATCH (1 cell(s), first notes!B3); Excel cannot calculate them, so the workbook was "
                         "not opened in Excel and not recalculated")
    assert f.fix.startswith("use the replacement references/functions.md names (for JOIN the & operator; for "
                            "REGEXMATCH ISNUMBER(SEARCH(<text>,<cell>)) in a helper column: it tests whether the cell "
                            "holds the text, without regard to case, so it gives REGEXMATCH's result only for a "
                            "pattern that is plain text) to verify the model in Excel; otherwise report 'audited, not "
                            "recalculated: functions only Google Sheets has'")


def test_every_function_of_the_list_is_found(tmp_path: Path) -> None:
    import openpyxl
    names = functions.sheets_only_names()
    book = openpyxl.Workbook()
    ws = book.active
    for i, name in enumerate(names, 1):
        ws.cell(i, 1).value = f"={name.lower()}(B1)"     # any case, as another tool may write it
    path = tmp_path / "all.xlsx"
    book.save(path)
    found = check.sheets_only_cells(path)
    assert sorted(found) == sorted(names) and all(v["count"] == 1 for v in found.values()), found


def test_a_workbook_without_such_a_function_gives_nothing(tmp_path: Path) -> None:
    root = _built_starter(tmp_path)
    assert check.sheets_only_cells(root / "model.xlsx") == {}


def test_the_list_check_uses_equals_the_catalogue_rows_with_excel_no_and_sheets_yes() -> None:
    text = _read(REFS / "functions.md")
    listed = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and re.fullmatch(r"[A-Z][A-Z0-9.]*", cells[0]) and cells[1] == "no" and cells[2] == "yes":
            listed.append(cells[0])
    assert listed and sorted(listed) == sorted(functions.sheets_only_names()), listed
    assert {"JOIN", "SPLIT", "REGEXMATCH", "QUERY"} <= set(listed)
    for name in listed:
        e = functions.info(name)
        assert e.excel == "no" and e.sheets == "yes" and e.replacement, name


# ============================================================================= [A-aera-8] documents (T-21, T-20)

T21 = ["A model that uses a function only Google Sheets has (JOIN, SPLIT, REGEXMATCH and the others this catalogue "
       "marks Excel \"no\") cannot be verified in Excel: build prints I-SHEETS-ONLY, and rotm.py check reports "
       "X-SHEETS-ONLY, does not open the workbook in Excel, leaves the file byte for byte and exits 3 (\"audited, "
       "not recalculated: functions only Google Sheets has\").",
       "Prefer the alternative the catalogue names (for JOIN the & operator).",
       "Such a model is listed under \"Not verified\"."]


def test_functions_md_states_what_is_verified_for_such_a_model() -> None:
    text = _plain(_section(_read(REFS / "functions.md"), "## Profiles"))
    missing = [t for t in T21 if t not in text]
    assert not missing, missing
    assert "The functions only Google Sheets has (Excel no, Sheets yes): " + ", ".join(
        functions.sheets_only_names()) + "." in text


def test_google_sheets_md_states_what_is_verified_for_such_a_model() -> None:
    text = _plain(_read(REFS / "google-sheets.md"))
    t21 = [t.replace("the others this catalogue", "the others that references/functions.md") for t in T21]
    missing = [t for t in t21 if t not in text]
    assert not missing, missing
    kept = _plain(_section(_read(REFS / "google-sheets.md"), "## Models kept in Google Sheets"))
    assert "A download that holds a function only Google Sheets has, written as a call in a formula: check reports " \
           "X-SHEETS-ONLY and does not recalculate it in Excel" in kept, kept


@needs_plugin
def test_check_skill_states_x_sheets_only() -> None:
    text = _plain(_read(CHECK_SKILL))
    for needed in ("A workbook that holds a function only Google Sheets has: check reports X-SHEETS-ONLY "
                   "(information) with the functions and their cells, does not open the workbook in Excel, leaves "
                   "the file byte for byte and exits 3 (not recalculated).",
                   "Say in the report \"audited, not recalculated: functions only Google Sheets has\"."):
        assert needed in text, needed


# ============================================================================= T-18 and T-19 (excel-power-query.md)

T18 = ("A rebuild when the source of a live query cannot be reached, or when Excel is not available: build keeps the "
       "earlier workbook with its live query as <stem>.bak_<time>.xlsx and its manifest as "
       "<stem>.manifest.bak_<time>.json, writes the new workbook with static values on that raw sheet, and warns "
       "W-PQ-NOT-ATTACHED (strict exit 1), naming both copies. To go back, copy both over the new files. To go on, "
       "make the source reachable and run rotm.py land \"<model>\" --table <t> --power-query. After a rebuild in which "
       "every query is attached again, no copy is left.")


def test_power_query_reference_states_the_rebuild_when_the_source_cannot_be_reached() -> None:
    assert T18 in _plain(_read(EPQ))


# The systems named in T-19, as a contract's source.system may name them.
WITH_FOLDING = ["Google BigQuery", "SQL Server", "Azure SQL", "Synapse", "PostgreSQL", "Snowflake",
                "Amazon Redshift", "SAP HANA"]
WITHOUT_FOLDING = ["Oracle", "MySQL"]
UNKNOWN_SYSTEM = "Teradata"


def _t19_sentences() -> tuple[str, str]:
    text = _plain(_section(_read(EPQ), "## How the plugin lands a table in Excel"))
    m = re.search(r"With \[EnableFolding = true\]: ([^.]*)\. Without it: ([^.]*?)\(", text)
    assert m, "excel-power-query.md: no T-19 sentences 'With [EnableFolding = true]: ...' and 'Without it: ...'"
    return m.group(1), m.group(2)


def _m_text_for(system: str) -> str:
    contract = common.read_json(SKILL / "templates" / "starter" / "contracts" / "lines.contract.json")
    contract["source"] = dict(contract.get("source") or {}, system=system)
    return land.m_text(contract)


def test_enable_folding_is_written_exactly_for_the_systems_the_reference_names() -> None:
    with_text, without_text = _t19_sentences()
    for name in WITH_FOLDING + WITHOUT_FOLDING + [UNKNOWN_SYSTEM]:
        folding = "[EnableFolding = true]" in _m_text_for(name)
        assert folding == (name in with_text), (name, folding, with_text)
        if name != UNKNOWN_SYSTEM:
            assert folding != (name in without_text), (name, folding, without_text)
    assert "a system land does not know" in without_text, without_text
    # every connector of land has a name in the reference
    for keys, _expr, fold, _q in land._PQ_CONNECTORS:
        named = [n for n in WITH_FOLDING + WITHOUT_FOLDING if any(k in n.lower() for k in keys)]
        assert named, keys
        assert all((n in with_text) == fold for n in named), (keys, named)


def test_power_query_reference_keeps_the_dataverse_condition() -> None:
    text = _plain(_read(EPQ))
    assert "Dataverse (only when using enhanced compute)" in text
    lands = _plain(_section(_read(EPQ), "## How the plugin lands a table in Excel"))
    assert ("Microsoft's list of the systems for which EnableFolding keeps folding also names Dataverse, only when "
            "using enhanced compute (platforms B1.4, confirmed for this part); land has no Dataverse connector."
            in lands), lands
    assert "the M text is Value.NativeQuery(<source>, <SQL>, null, [EnableFolding = true]), with" not in lands

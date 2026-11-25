"""Tests of the v6 check and land changes (package P3 of the v6 plan). Owner: P3.

Entries: [A-aera-2] (the formula of the query the workbook holds is read back when rotm.py attaches it, recorded as
the raw entry's query_formula_sha256 (land.query_formula_sha256), and compared at check --refresh before the refresh:
rule X-QUERY-CHANGED) and [C-sol-6, J-1] part 1 (rule I-HIDDEN-DECIMALS after a refresh that ran and after land);
supporting [A-aera-5] (a logical value stored in a text column is D-TYPE and is tagged in the digest, LOGICAL_TAG).
Tests marked excel drive Excel through lib.excel. The names of the first tests of each entry are fixed by the v6
plan (section 5.3) and quoted by the documents.
"""
from __future__ import annotations

import contextlib
import csv
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import check, common, excel, land, manifest
import test_check as tc
from test_v4_check import _built_starter, _rows, _total, _write_rows, starter
from test_v5_check import _set_query_fields

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
STARTER = SKILL / "templates" / "starter"


# ----------------------------------------------------------------------------- helpers

def rules(rep) -> list[str]:
    return [f.rule for f in rep.findings]


def lines(rep) -> list[str]:
    return [f.line() for f in rep.findings] + list(rep.notes)


def out(r) -> str:
    return ((r.stdout or "") + (r.stderr or ""))[-4000:]


def raw_entry(root: Path, table: str = "lines") -> dict:
    return manifest.raw_sheet(json.loads((root / "model.manifest.json").read_text(encoding="utf-8")), table)


def three_files(root: Path, table: str = "lines") -> dict[str, bytes | None]:
    """raw/<t>.csv, raw/<t>.controls.json (None when absent) and the manifest, byte for byte."""
    paths = [root / "raw" / f"{table}.csv", root / "raw" / f"{table}.controls.json", root / "model.manifest.json"]
    return {p.name: (p.read_bytes() if p.is_file() else None) for p in paths}


def workbook_formula(wb: Path, query: str = "q_lines") -> str | None:
    inv = excel.inventory(wb)
    return next((q.get("formula") for q in inv.get("query_formulas") or [] if q.get("name") == query), None)


def attached_starter(tmp_path: Path) -> Path:
    """The starter built without Power Query, then its query attached with rotm.py land --power-query."""
    root = _built_starter(tmp_path)
    r = tc.run_cli("land", str(root), "--table", "lines", "--power-query")
    assert r.returncode == 0, out(r)
    return root


def report_of(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


SORTED_STEP = ('"en-US")\nin\n    Typed', '"en-US"),\n    Sorted = Table.Sort(Typed, {{"line_id", Order.Descending}})\nin'
                                         '\n    Sorted')


def fake_excel(monkeypatch: pytest.MonkeyPatch, *, formulas: dict[str, str | None] | Exception | None = None,
               qtables: dict[str, str | None] | None = None) -> SimpleNamespace:
    """Excel as available without starting it: check._excel_job as if Excel had recalculated (its pq_refresh step
    refreshed no table), excel.inventory giving `formulas` (or raising it), check.query_tables giving `qtables`,
    excel.excel_lock without the lock file. Returns the operations of each main job and the inventory calls."""
    monkeypatch.delenv("ROTM_NO_EXCEL", raising=False)
    monkeypatch.setattr(excel, "excel_status", lambda: SimpleNamespace(available=True, reason=""))
    monkeypatch.setattr(excel, "excel_lock", lambda *a, **k: contextlib.nullcontext())
    seen = SimpleNamespace(jobs=[], inventories=0)

    def job(wb, ops, read_refs, timeout_s=None):
        seen.jobs.append(list(ops))
        steps = [{"op": "pq_refresh", "ok": True, "tables": [], "connections": []}] if any(
            o.get("op") == "pq_refresh" for o in ops) else []
        res = excel.JobResult(ok=True, pid=4242, steps=steps + [{"op": "recalc", "ok": True}], excel_version="16.0")
        return res, {ref: 0 for ref in read_refs}, [], 0

    def inventory(_wb):
        seen.inventories += 1
        if isinstance(formulas, Exception):
            raise formulas
        return {"query_formulas": [{"name": k, "formula": v} for k, v in (formulas or {}).items()], "sheets": []}
    monkeypatch.setattr(check, "_excel_job", job)
    monkeypatch.setattr(excel, "inventory", inventory)
    if qtables is not None:
        monkeypatch.setattr(check, "query_tables", lambda _wb: dict(qtables))
    return seen


M_TEXT = "let\n    Source = 1\nin\n    Source"


def rotm_attached(root: Path, formula_text: str | None = M_TEXT) -> dict:
    """The raw entry as land --power-query v6 records it (query_formula_sha256 of `formula_text`), without Excel."""
    entry = _set_query_fields(root)
    man = manifest.load_manifest(root / "model.xlsx")
    e = manifest.raw_sheet(man, "lines")
    if formula_text is None:
        e.pop("query_formula_sha256", None)
    else:
        e["query_formula_sha256"] = land.query_formula_sha256(formula_text)
    manifest.write_manifest(root / "model.xlsx", man)
    return dict(entry, **{k: v for k, v in e.items()})


# ============================================================================= [A-aera-2] the query read back

def test_query_formula_hash_makes_line_ends_and_trailing_white_space_uniform() -> None:
    plain = land.query_formula_sha256("let\n    Source = 1\nin\n    Source")
    assert re.fullmatch(r"[0-9a-f]{64}", plain)
    assert land.query_formula_sha256("let  \r\n    Source = 1\t\r\nin\r    Source\r\n\r\n") == plain
    assert land.query_formula_sha256("let\n    Source = 2\nin\n    Source") != plain
    assert land.query_formula_sha256("let\n\n    Source = 1\nin\n    Source") != plain     # inner lines count


@pytest.mark.excel
def test_attach_records_the_formula_hash(tmp_path: Path) -> None:
    root = attached_starter(tmp_path)
    recorded = raw_entry(root).get("query_formula_sha256")
    assert recorded and re.fullmatch(r"[0-9a-f]{64}", recorded), raw_entry(root)
    reads = [workbook_formula(root / "model.xlsx") for _ in range(2)]
    assert all(isinstance(f, str) and f.strip() for f in reads), reads
    assert [land.query_formula_sha256(f) for f in reads] == [recorded, recorded]


@pytest.mark.excel
def test_query_changed_in_excel_is_found_at_refresh(tmp_path: Path) -> None:
    root = attached_starter(tmp_path)
    wb = root / "model.xlsx"
    formula = (workbook_formula(wb) or "").replace("\r\n", "\n")
    assert SORTED_STEP[0] in formula, formula[-300:]
    changed = formula.replace(*SORTED_STEP)
    # the query changed through Excel without a rotm.py command (as in Excel's Power Query editor): the same query and
    # table names, another M text; the table is refreshed with the sorted rows
    res = excel.pq_attach(wb, sheet="raw_lines", table_name="tbl_raw_lines", query_name="q_lines", m_text=changed)
    assert res.ok, res.message()
    before = three_files(root)
    report = tmp_path / "check.json"
    r = tc.run_cli("check", str(root), "--refresh", "--accept-refresh", "--strict", "--json", str(report))
    assert r.returncode != 0, out(r)
    rep = report_of(report)
    found = [f for f in rep["findings"] if f["rule"] == "X-QUERY-CHANGED"]
    assert found, [f["rule"] for f in rep["findings"]]
    f = found[0]
    assert f["severity"] == "error" and f["sheet"] == "raw_lines"
    assert f["message"].startswith("raw_lines: the query q_lines that the workbook holds differs from the query "
                                   "rotm.py attached (manifest query_formula_sha256 sha256:"), f["message"]
    assert "no table was refreshed and nothing was accepted for raw_lines" in f["message"]
    assert "put the change into queries/lines.pq.m and run" in f["fix"] and "--table lines --power-query" in f["fix"]
    assert rep["data"]["refreshed"] is False
    assert not any(x["rule"] == "R-ACCEPTED" for x in rep["findings"])
    assert three_files(root) == before


@pytest.mark.excel
def test_unchanged_query_refresh_cycle_exits_0(tmp_path: Path) -> None:
    """The commands of selftest step f27 (lib/selftest.py step_f27) with an unchanged query: every step exits as in
    f27; build and land record query_formula_sha256 and every check --refresh compares it (no X-QUERY-CHANGED)."""
    from lib.selftest import _amount_total, _more_lines, _read_csv, _write_csv
    root = tmp_path / "f27" / "model"
    root.parent.mkdir(parents=True)

    def run(*args: str, expect: int = 0) -> dict:
        rep_path = root.parent / f"step_{len(list(root.parent.glob('step_*.json'))):02d}.json"
        r = tc.run_cli(*args, "--json", str(rep_path))
        assert r.returncode == expect, (args, out(r))
        return report_of(rep_path)

    r = tc.run_cli("init", str(root), "--template", "starter", "--name", "v6_cycle")
    assert r.returncode == 0, out(r)
    reply = "agreed for the v6 cycle test"
    task = root.parent / "task.md"
    task.write_text(f"The user's reply to the contract: {reply}\n", encoding="utf-8")
    run("contract", "agree", str(root), "--table", "lines", "--reply", reply, "--by", "tester", "--channel",
        "task-file", "--source-file", str(task))
    run("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"))
    spec_path = root / "model.spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for sh in spec["sheets"]:
        if sh.get("type") == "raw":
            sh["landing"] = "power_query"
    spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    raw_csv = root / "raw" / "lines.csv"
    base = _read_csv(raw_csv)
    run("build", str(root), "--strict", "--force")
    assert raw_entry(root).get("query_formula_sha256"), raw_entry(root)        # build's attach records it
    run("check", str(root))
    grown = base + _more_lines(base, 15)
    _write_csv(raw_csv, grown)
    rep = run("check", str(root), "--refresh", expect=1)
    assert rep["data"]["refreshed"] is True and "H-COVERAGE" in {f["rule"] for f in rep["findings"]}
    assert "X-QUERY-CHANGED" not in {f["rule"] for f in rep["findings"]}
    run("extend", str(root))
    run("check", str(root), "--accept-refresh", "--control", f"lines.amount={_amount_total(grown)}")
    shrunk = base[:40]
    _write_csv(raw_csv, shrunk)
    rep = run("check", str(root), "--refresh", expect=1)
    assert rep["data"]["refreshed"] is True and "H-BEYOND" in {f["rule"] for f in rep["findings"]}
    assert "X-QUERY-CHANGED" not in {f["rule"] for f in rep["findings"]}
    run("extend", str(root))
    run("check", str(root), "--accept-refresh", "--control", f"lines.amount={_amount_total(shrunk)}")
    run("build", str(root), "--strict")
    assert raw_entry(root).get("query_formula_sha256"), raw_entry(root)
    run("check", str(root), "--strict")
    run("audit", str(root), "--strict")


def test_manifest_without_formula_hash_gets_a_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _built_starter(tmp_path)
    _set_query_fields(root)                                         # as land --power-query v5 recorded it
    assert "query_formula_sha256" not in raw_entry(root)
    rep = check.check_model(root, refresh=True)                     # not refused
    note = next(n for n in rep.notes if "records no hash of the query formula" in n)
    assert note.startswith("--refresh: raw_lines's manifest records no hash of the query formula the workbook holds "
                           "(attached before v6), so the workbook's query was not compared with the attached text; "), \
        note
    assert note.endswith("--table lines --power-query attaches it again and records the hash")
    assert "X-QUERY-CHANGED" not in rules(rep)


def test_hand_attached_query_never_carried_over_keeps_its_note_and_is_not_compared(tmp_path: Path,
                                                                                     monkeypatch) -> None:
    root = _built_starter(tmp_path)
    man = manifest.load_manifest(root / "model.xlsx")
    manifest.raw_sheet(man, "lines").update(landing="power_query", query_attach="hand", query_name="q_hand")
    manifest.write_manifest(root / "model.xlsx", man)
    seen = fake_excel(monkeypatch, formulas=AssertionError("the formula was read"), qtables={"raw_lines": "q_hand"})
    rep = check.check_model(root, refresh=True)
    assert seen.inventories == 0 and "X-QUERY-CHANGED" not in rules(rep), lines(rep)
    assert any("raw_lines's query q_hand was attached by hand" in n for n in rep.notes), lines(rep)
    assert not any("records no hash of the query formula" in n for n in rep.notes)
    assert any(o.get("op") == "pq_refresh" for o in seen.jobs[0])


def test_changed_formula_is_x_query_changed_without_excel(tmp_path: Path, monkeypatch) -> None:
    root = _built_starter(tmp_path)
    rotm_attached(root)
    seen = fake_excel(monkeypatch, formulas={"q_lines": M_TEXT + "\n// changed in Excel"},
                      qtables={"raw_lines": "q_lines"})
    before = three_files(root)
    rep = check.check_model(root, refresh=True)
    found = [f for f in rep.findings if f.rule == "X-QUERY-CHANGED"]
    assert len(found) == 1 and found[0].severity == "warning", lines(rep)
    assert seen.inventories == 1 and not any(o.get("op") == "pq_refresh" for o in seen.jobs[0])
    assert rep.data["refreshed"] is False
    assert any("no table was accepted: --refresh was asked for and no refresh ran (a query the workbook holds differs"
               in n for n in rep.notes), lines(rep)
    rep = check.check_model(root, refresh=True, accept_refresh=True)
    found = [f for f in rep.findings if f.rule == "X-QUERY-CHANGED"]
    assert found and found[0].severity == "error" and rep.exit_code == 1
    assert three_files(root) == before


def test_equal_formula_refreshes_without_excel(tmp_path: Path, monkeypatch) -> None:
    root = _built_starter(tmp_path)
    rotm_attached(root)
    # Excel gives the text back with other line ends and trailing white space: the same hash
    seen = fake_excel(monkeypatch, formulas={"q_lines": M_TEXT.replace("\n", "  \r\n") + "\r\n"},
                      qtables={"raw_lines": "q_lines"})
    rep = check.check_model(root, refresh=True)
    assert "X-QUERY-CHANGED" not in rules(rep), lines(rep)
    assert seen.inventories == 1 and any(o.get("op") == "pq_refresh" for o in seen.jobs[0])


@pytest.mark.parametrize("formulas", [{"q_lines": None}, {}, excel.ExcelJobFailed("the workbook was not inspected")],
                         ids=["no formula", "no query", "inventory failed"])
def test_unreadable_formula_is_x_query_changed_without_excel(tmp_path: Path, monkeypatch, formulas) -> None:
    root = _built_starter(tmp_path)
    rotm_attached(root)
    seen = fake_excel(monkeypatch, formulas=formulas, qtables={"raw_lines": "q_lines"})
    rep = check.check_model(root, refresh=True)
    found = [f for f in rep.findings if f.rule == "X-QUERY-CHANGED"]
    assert found and found[0].message == (
        "raw_lines: the formula of the query q_lines could not be read back from the workbook, so it could not be "
        "compared with the query rotm.py attached; no table was refreshed and nothing was accepted for raw_lines")
    assert not any(o.get("op") == "pq_refresh" for o in seen.jobs[0])


def test_attach_without_an_excel_process_records_no_formula_hash(tmp_path: Path, monkeypatch) -> None:
    """A job result that names no Excel process did not come from Excel: nothing is read back, no hash is recorded
    (an earlier one is removed) and the note says so."""
    root = _built_starter(tmp_path)
    rotm_attached(root)
    assert raw_entry(root).get("query_formula_sha256")
    monkeypatch.setattr(excel, "excel_status", lambda: excel.ExcelStatus(available=True, version="16.0"))
    monkeypatch.setattr(excel, "pq_attach", lambda workbook, **kw: excel.JobResult(
        ok=True, pid=None, steps=[{"op": "pq_attach", "query": kw["query_name"], "table": kw["table_name"]}]))
    monkeypatch.setattr(excel, "inventory", lambda _wb: (_ for _ in ()).throw(AssertionError("Excel was asked")))
    rep = land.attach_power_query(root / "model.xlsx", root, "lines")
    assert "query_formula_sha256" not in raw_entry(root)
    assert "the formula of q_lines could not be read back from the workbook; check --refresh will not compare it" \
        in rep.notes


def test_attach_records_the_hash_of_the_formula_read_back(tmp_path: Path, monkeypatch) -> None:
    root = _built_starter(tmp_path)
    monkeypatch.setattr(excel, "excel_status", lambda: excel.ExcelStatus(available=True, version="16.0"))
    sent: list[str] = []

    def pq_attach(workbook, **kw):
        sent.append(kw["m_text"])
        return excel.JobResult(ok=True, pid=4242, steps=[{"op": "pq_attach", "query": kw["query_name"]}])
    monkeypatch.setattr(excel, "pq_attach", pq_attach)
    monkeypatch.setattr(excel, "inventory", lambda _wb: {"query_formulas": [
        {"name": "q_other", "formula": "let x = 2 in x"}, {"name": "q_lines", "formula": sent[0].rstrip("\n")}]})
    land.attach_power_query(root / "model.xlsx", root, "lines")
    assert raw_entry(root)["query_formula_sha256"] == land.query_formula_sha256(sent[0])


# ============================================================================= [C-sol-6, J-1] part 1 hidden decimals

def test_shown_and_held_decimals() -> None:
    assert check.shown_decimals('#,##0_);(#,##0);"-"_)') == 0
    assert check.shown_decimals('#,##0.00_);(#,##0.00);"-"_)') == 2
    assert check.shown_decimals("0.0%") == 3 and check.shown_decimals('0.0"%"') == 1
    assert check.shown_decimals("0.0000") == 4 and check.shown_decimals("#,##0,") == -3
    for code in ("General", "yyyy-mm-dd", "@", "0.00E+00", '"text"'):
        assert check.shown_decimals(code) is None, code
    assert check.value_decimals(858800.25) == 2 and check.value_decimals(0.1 + 0.2) == 1
    assert check.value_decimals(7.0) == 0 and check.value_decimals(True) == 0 and check.value_decimals("1.5") == 0


def _fraction_rows(n_fractions: int = 2, step: str = "0.25") -> list[dict]:
    """The starter's sample rows with `step` added to the first row's amount and taken from the next rows', so the
    total stays the same."""
    rows = _rows(STARTER / "data" / "lines.csv")
    from decimal import Decimal
    d = Decimal(step)
    rows[0]["amount"] = str(Decimal(rows[0]["amount"]) + d * n_fractions)
    for k in range(1, n_fractions + 1):
        rows[k]["amount"] = str(Decimal(rows[k]["amount"]) - d)
    return rows


def test_land_with_fractions_into_a_whole_number_column_gets_the_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _built_starter(tmp_path)
    assert raw_entry(root)["formats"][3] == "amount"                 # the landed amounts are whole numbers
    rows = _fraction_rows()
    src = _write_rows(tmp_path / "export" / "lines.csv", rows)
    r = tc.run_cli("land", str(root), "--table", "lines", "--csv", str(src), "--force", "--strict", "--json", "-")
    assert r.returncode == 0, out(r)                                # information only: the strict exit code stays 0
    rep = json.loads(r.stdout)
    found = [f for f in rep["findings"] if f["rule"] == "I-HIDDEN-DECIMALS"]
    assert len(found) == 1 and found[0]["severity"] == "info", rep["findings"]
    f = found[0]
    first = format(float(rows[0]["amount"]), ".15g")
    assert f["message"] == (f"raw_lines column amount: 3 value(s) hold more decimals than its number format amount "
                            f"(#,##0_);(#,##0);\"-\"_)) shows (first row 2: {first}); the values are stored and "
                            "calculated in full"), f["message"]
    assert f["sheet"] == "raw_lines" and f["cell"] == "D2" and f["count"] == 3
    assert "build \"" in f["fix"] and "--force): build gives the column a format that shows the decimals" in f["fix"]


def test_land_into_a_column_whose_format_shows_the_decimals_gets_no_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter(tmp_path / "m", rows=_fraction_rows(1, "0.5"))
    from lib import build
    assert build.build(root / "model.spec.json", power_query=False).counts()["errors"] == 0
    assert raw_entry(root)["formats"][3] == "amount2"               # build chose two decimals
    rows = _fraction_rows(2, "0.25")
    rep = land.land_csv(root, "lines", _write_rows(tmp_path / "e1" / "lines.csv", rows), force=True,
                        controls=[f"lines.amount={_total(rows)}"])
    assert rep.exit_code == 0 and "I-HIDDEN-DECIMALS" not in rules(rep), lines(rep)
    rows = _fraction_rows(2, "0.125")                               # three decimals: more than amount2 shows
    rep = land.land_csv(root, "lines", _write_rows(tmp_path / "e2" / "lines.csv", rows), force=True,
                        controls=[f"lines.amount={_total(rows)}"])
    assert rules(rep).count("I-HIDDEN-DECIMALS") == 1, lines(rep)


def test_a_format_the_contract_names_gets_the_contract_fix(tmp_path: Path) -> None:
    from lib import dictionary
    entry = {"name": "raw_lines", "columns": ["line_id", "amount"], "types": ["text", "number"],
             "formats": ["text", "amount"]}
    contract = {"columns": [{"name": "line_id", "type": "text"}, {"name": "amount", "type": "number",
                                                                  "format": "amount"}]}
    found = check.hidden_decimal_findings(tmp_path, "lines", entry, ["line_id", "amount"],
                                          [["L1", 10.0], ["L2", 2.5], ["L3", 1.25]], contract,
                                          dictionary.load_dictionary())
    assert len(found) == 1 and found[0].count == 2 and found[0].cell == "B3"
    assert found[0].fix == ("the format comes from contracts/lines.contract.json (column amount, \"format\": "
                            "\"amount\"): agree a format that shows the decimals in the contract, then rebuild")
    entry["formats"] = ["text", "general"]
    assert check.hidden_decimal_findings(tmp_path, "lines", entry, ["line_id", "amount"], [["L2", 2.5]], contract,
                                         dictionary.load_dictionary()) == []


@pytest.mark.excel
def test_refresh_with_fractions_gets_the_note_and_keeps_the_exit_code(tmp_path: Path) -> None:
    root = attached_starter(tmp_path)
    raw_csv = root / "raw" / "lines.csv"               # the file the attached query reads
    base = list(csv.reader(raw_csv.open(encoding="utf-8", newline="")))

    def refresh_with(delta: str) -> dict:
        rows = [list(r) for r in base]
        from decimal import Decimal
        rows[1][3] = str(Decimal(rows[1][3]) + 2 * Decimal(delta))      # rows[0] is the header
        rows[2][3] = str(Decimal(rows[2][3]) - Decimal(delta))
        rows[3][3] = str(Decimal(rows[3][3]) - Decimal(delta))
        with raw_csv.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerows(rows)
        path = tmp_path / f"check_{delta}.json"
        r = tc.run_cli("check", str(root), "--refresh", "--accept-refresh", "--strict", "--json", str(path))
        rep = report_of(path)
        assert rep["data"]["refreshed"] is True, out(r)
        return {"code": r.returncode, "rep": rep}

    whole = refresh_with("1")
    assert not [f for f in whole["rep"]["findings"] if f["rule"] == "I-HIDDEN-DECIMALS"]
    frac = refresh_with("0.25")
    found = [f for f in frac["rep"]["findings"] if f["rule"] == "I-HIDDEN-DECIMALS"]
    assert frac["code"] == whole["code"], (frac["code"], whole["code"])
    assert len(found) == 1 and found[0]["severity"] == "info" and found[0]["count"] == 3, frac["rep"]["findings"]
    assert found[0]["message"].startswith("raw_lines column amount: 3 value(s) hold more decimals than its number "
                                          "format amount")
    assert found[0]["cell"] == "D2"
    # no format is changed by the refresh: the cell keeps the whole-number format
    import openpyxl
    ws = openpyxl.load_workbook(root / "model.xlsx")["raw_lines"]
    assert ws["D2"].number_format == ws["D5"].number_format


# ============================================================================= supporting [A-aera-5] logical values

def test_logical_value_in_a_text_column_is_d_type_and_tagged(tmp_path: Path) -> None:
    rows = [["L1", "x"], ["L2", True], ["L3", False], ["L4", "TRUE"]]
    found, digest_rows = check._stored_as_text(tmp_path, "lines", "raw_lines", ["line_id", "note"], rows,
                                               ["text", "text"])
    assert [f.rule for f in found] == ["D-TYPE"]
    assert found[0].message == "2 cell(s) of text column note in raw_lines are stored as logical values (first B3: TRUE)"
    assert found[0].cell == "B3" and found[0].data["stored_as_logical"] == 2
    assert digest_rows[1][1] == check.LOGICAL_TAG + "TRUE" and digest_rows[2][1] == check.LOGICAL_TAG + "FALSE"
    assert digest_rows[3][1] == "TRUE"                                   # the text TRUE is not tagged
    plain = [["L1", "x"], ["L4", "TRUE"]]
    found, digest_rows = check._stored_as_text(tmp_path, "lines", "raw_lines", ["line_id", "note"], plain,
                                               ["text", "text"])
    assert found == [] and digest_rows is plain                          # matching cells: the digest stays


@pytest.mark.excel
def test_text_cell_stored_as_a_logical_value_is_found_by_check(tmp_path: Path) -> None:
    import openpyxl
    rows = _rows(STARTER / "data" / "lines.csv")
    rows[1]["line_id"] = "TRUE"                                          # a text key that reads TRUE
    root = starter(tmp_path / "m", rows=rows)
    from lib import build
    assert build.build(root / "model.spec.json", power_query=False).counts()["errors"] == 0
    wb = root / "model.xlsx"
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0, lines(rep)                                # the model as built passes
    book = openpyxl.load_workbook(wb)
    assert book["raw_lines"]["A3"].value == "TRUE"
    book["raw_lines"]["A3"].value = True                                 # the logical value, typed into the raw sheet
    book.save(wb)
    res = excel.recalc(wb, save=True)                                    # saved by Excel
    assert res.ok, res.errors
    assert openpyxl.load_workbook(wb)["raw_lines"]["A3"].value is True
    rep = check.check_model(root, strict=True)
    assert rep.exit_code != 0, lines(rep)
    found = [f for f in rep.findings if f.rule == "D-TYPE" and f.sheet == "raw_lines"]
    assert found and found[0].cell == "A3" and "stored as logical values (first A3: TRUE)" in found[0].message, \
        lines(rep)
    assert not any(n.startswith("raw_lines:") and "unchanged since landing" in n for n in rep.notes), lines(rep)

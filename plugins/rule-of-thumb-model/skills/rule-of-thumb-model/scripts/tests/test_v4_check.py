"""Tests of the v4 check changes (package P3 of the v4 plan). Owner: P3.

Entries: [A-aera-3, C-sol-2, C-sol-6] (the acceptance of refreshed rows: a query attached by hand recorded on its
first load, rows accepted only from a live query table, nothing accepted under CT-VERSION), [A-aera-1] (labels that
Excel's criteria matching merges: lib/criteria.py, rule D-CRITERIA), [C-sol-5] (the summary line); supporting
[C-sol-3] part 2 (X-OPEN), [A-aera-8, A-aera-9] part 2 (a text column stored as numbers), [C-sol-12, C-sol-13, J-1]
(check's words for a raised alert). Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import csv
import datetime as dt
import shutil
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import audit, build, check, common, controls, criteria, land, manifest, modelfolder
from test_build import agree, base_spec, make_model as build_model, sales_rows
from test_v2_raw_layer import fake_excel
import test_check as tc

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
STARTER = SKILL / "templates" / "starter"


# ----------------------------------------------------------------------------- helpers

def rules(rep) -> list[str]:
    return [f.rule for f in rep.findings]


def lines(rep) -> list[str]:
    return [f.line() for f in rep.findings] + list(rep.notes)


def _files(*paths: Path) -> dict[str, bytes | None]:
    return {p.as_posix(): (p.read_bytes() if p.is_file() else None) for p in paths}


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def _total(rows: list[dict]) -> float:
    total = sum((Decimal(r["amount"]) for r in rows if r["amount"] != ""), Decimal(0))
    return int(total) if total == total.to_integral_value() else float(total)


def starter(root: Path, *, rows: list[dict] | None = None, categories: list[str] | None = None,
            spec_edit=None) -> Path:
    """A copy of the starter template (rotm.py init --template starter) with its contract agreed in a session for
    the control total of `rows`, the rows landed; `categories` replaces the input list of categories (and drops
    the contract's allowed values of category); `spec_edit` changes the specification."""
    modelfolder.init_model(root, name="starter_v4", template="starter")
    rows = rows if rows is not None else _rows(STARTER / "data" / "lines.csv")
    path = root / "contracts" / "lines.contract.json"
    c = common.read_json(path)
    c.pop("shown_sha256", None)
    c["expect"]["totals"][0]["value"] = _total(rows)
    if categories is not None:
        next(col for col in c["columns"] if col["name"] == "category")["allowed"] = None
    common.write_json(path, agree(c))
    src = _write_rows(root.parent / f"{root.name}_extract.csv", rows)
    rep = land.land_csv(root, "lines", src)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    if categories is not None or spec_edit is not None:
        spec = common.read_json(root / "model.spec.json")
        if categories is not None:
            items = next(s for s in spec["sheets"] if s["type"] == "inputs")["items"]
            next(i for i in items if i["id"] == "categories")["values"] = list(categories)
        if spec_edit is not None:
            spec_edit(spec)
        common.write_json(root / "model.spec.json", spec)
    return root


def _built_starter(tmp_path: Path, **kw) -> Path:
    root = starter(tmp_path / "m", **kw)
    rep = build.build(root / "model.spec.json", power_query=False)
    assert rep.counts()["errors"] == 0, lines(rep)
    return root


def _pq_landing(spec: dict) -> None:
    next(s for s in spec["sheets"] if s.get("name") == "raw_lines")["landing"] = "power_query"


# ============================================================================= 1. [A-aera-3, C-sol-2, C-sol-6]

def test_hand_attached_rule_follows_plan_5_3() -> None:
    q = {"raw_sales": "q_sales"}
    rotm = {"name": "raw_sales", "landing": "power_query", "query_name": "q_sales", "query_contract_sha256": "ab"}
    assert not check._hand_attached(rotm, q)                                          # attached by land
    assert check._hand_attached(dict(rotm, query_attach="hand"), q)                  # (a) recorded as hand
    assert check._hand_attached(dict(rotm, query_attach="hand"), {})                 # (a) also with the table gone
    assert check._hand_attached({"name": "raw_sales", "landing": "static"}, q)       # (b) found on a static sheet
    assert check._hand_attached(dict(rotm, query_name="q_other"), q)                 # (b) another query's table
    assert check._hand_attached({k: v for k, v in rotm.items() if k != "query_contract_sha256"}, q)   # (b) no hash
    assert not check._hand_attached({"name": "raw_sales", "landing": "power_query"}, {})          # no table


@pytest.mark.excel
def test_first_acceptance_of_a_hand_attached_query_records_it(tmp_path: Path) -> None:
    from lib import excel
    root = _built_starter(tmp_path)
    wb = root / "model.xlsx"
    src = tmp_path / "hand" / "lines.csv"
    src.parent.mkdir()
    shutil.copy2(root / "raw" / "lines.csv", src)              # the same rows as the landed snapshot
    c = common.read_json(root / "contracts" / "lines.contract.json")
    res = excel.pq_attach(wb, sheet="raw_lines", table_name="tbl_raw_lines", query_name="q_hand_lines",
                          m_text=land.m_text(c, csv_path=src))
    assert res.ok, res.message()
    assert check.query_tables(wb) == {"raw_lines": "q_hand_lines"}
    r = tc.run_cli("check", str(root), "--accept-refresh", "--strict")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "unchanged since landing" in r.stdout + r.stderr
    assert ("raw_lines: recorded in the manifest as loaded by the query q_hand_lines attached by hand "
            "(query_attach hand)") in r.stdout + r.stderr
    entry = manifest.raw_sheet(manifest.load_manifest(wb), "lines")
    assert (entry["landing"], entry["query_attach"], entry["query_name"]) == ("power_query", "hand", "q_hand_lines")
    assert "query_contract_sha256" not in entry
    # no undocumented query: X-FEATURE stays the information every documented query of a product "both" model gets
    # (Google Sheets loads static values), not the warning for a query the manifest does not record
    r = tc.run_cli("audit", str(root), "--strict")
    feature = [ln for ln in r.stdout.splitlines() if "X-FEATURE" in ln]
    assert r.returncode == 0 and all(ln.startswith("info") for ln in feature), r.stdout[-3000:] + r.stderr[-2000:]


def test_first_acceptance_of_a_hand_attached_query_without_excel(tmp_path: Path, monkeypatch) -> None:
    """The same rule without Excel (attach_query_table stands in for the query): the digest is unchanged and the
    hand-attached query is recorded; a run with the table already recorded writes nothing."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10)
    tc.attach_query_table(m.wb, "q_by_hand")
    rep = check.check_model(m.root, accept_refresh=True)
    assert "R-ACCEPTED" not in rules(rep) and "R-CHANGED" not in rules(rep), lines(rep)
    entry = manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")
    assert (entry["landing"], entry["query_attach"], entry["query_name"]) == ("power_query", "hand", "q_by_hand")
    assert "query_contract_sha256" not in entry
    assert ("raw_sales: recorded in the manifest as loaded by the query q_by_hand attached by hand "
            "(query_attach hand)") in rep.notes
    before = _files(manifest.manifest_path(m.wb))
    rep = check.check_model(m.root, accept_refresh=True)
    assert not any("attached by hand (query_attach hand)" in n for n in rep.notes)
    assert _files(manifest.manifest_path(m.wb)) == before
    # without --accept-refresh nothing is recorded
    m2 = tc.make_model(tmp_path / "b", 10)
    tc.attach_query_table(m2.wb, "q_by_hand")
    check.check_model(m2.root)
    assert manifest.raw_sheet(manifest.load_manifest(m2.wb), "sales")["landing"] == "static"


def _remove_query_table(wb: Path, cell: str | None = None, value=None) -> None:
    """The query table converted to a range (openpyxl stands in for Excel's Unlist), one raw cell typed over."""
    import openpyxl
    book = openpyxl.load_workbook(wb)
    ws = book["raw_sales"]
    del ws.tables["tbl_raw_sales"]
    if cell:
        ws[cell] = value
    book.save(wb)


def test_changed_rows_without_a_live_query_table_are_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, landing="power_query", query_table=True)
    assert check.query_tables(m.wb) == {"raw_sales": "q_sales"}
    _remove_query_table(m.wb, "A2", "new-extract-001")
    assert check.query_tables(m.wb) == {}
    snap, ctl = m.root / "raw" / "sales.csv", controls.path(m.root, "sales")
    watched = (snap, ctl, manifest.manifest_path(m.wb))
    before = _files(*watched)
    total = sum(100 + 7.5 * i for i in range(10))
    rep = check.check_model(m.root, accept_refresh=True, strict=True, controls={"sales.amount": total},
                            control_texts=[f"sales.amount={total}"])
    assert rep.exit_code == 1 and "R-ACCEPTED" not in rules(rep), lines(rep)
    f = next(x for x in rep.findings if x.rule == "R-CHANGED")
    assert f.sheet == "raw_sales" and f.message == (
        "raw_sales differs from the landed snapshot recorded in the manifest (10 rows recorded, 10 rows now); the "
        "manifest says a query loads it, but the workbook holds no query table at A1 of raw_sales (converted to a "
        "range or deleted), so the change is not accepted as a refresh")
    assert f"land \"{m.root.as_posix()}\" --table sales --power-query" in f.fix and "build" in f.fix
    assert _files(*watched) == before and before[ctl.as_posix()] is None


def test_controls_are_not_recorded_without_a_live_query_table(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, landing="power_query", query_table=True)
    _remove_query_table(m.wb)
    ctl = controls.path(m.root, "sales")
    total = sum(100 + 7.5 * i for i in range(10))
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total},
                            control_texts=[f"sales.amount={total}"])
    assert not ctl.exists()
    assert ("raw_sales: --accept-refresh: the --control totals were not recorded, because the workbook holds no "
            "query table at A1 of raw_sales") in rep.notes, rep.notes


def _agree_again(root: Path, table: str, edit) -> None:
    """The contract changed and agreed again (the user's reply recorded), as contract propose, show and agree do."""
    path = root / "contracts" / f"{table}.contract.json"
    c = common.read_json(path)
    edit(c)
    common.write_json(path, agree(c))


def test_contract_agreed_again_accepts_nothing_without_excel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, capacity=12, landing="power_query", query_table=True)
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))              # stands in for a refresh with 2 more rows
    _agree_again(m.root, "sales", lambda c: c["expect"]["rows"].update(max=50_000))
    snap, ctl = m.root / "raw" / "sales.csv", controls.path(m.root, "sales")
    watched = (snap, ctl, manifest.manifest_path(m.wb))
    before = _files(*watched)
    total = sum(100 + 7.5 * i for i in range(12))
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total},
                            control_texts=[f"sales.amount={total}"])
    assert rep.exit_code == 1 and "CT-VERSION" in rules(rep) and "R-ACCEPTED" not in rules(rep), lines(rep)
    assert ("raw_sales: nothing was accepted, because the workbook was built from another version of its contract "
            "(CT-VERSION); raw/sales.csv, raw/sales.controls.json and the manifest were not changed") in rep.notes
    assert _files(*watched) == before


@pytest.mark.excel
def test_contract_agreed_again_after_a_refresh_accepts_nothing(tmp_path: Path) -> None:
    root = starter(tmp_path / "m", spec_edit=_pq_landing)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data.get("power_query_attached") == ["lines"], lines(rep)
    rows = _rows(root / "raw" / "lines.csv")
    more = rows + [dict(rows[0], line_id="L9001")]
    _write_rows(root / "raw" / "lines.csv", more)          # the source changed; the query reads this file
    rep = check.check_model(root, refresh=True)
    assert rep.data["refreshed"] is True, lines(rep)
    _agree_again(root, "lines", lambda c: c["expect"]["totals"][0].update(value=_total(more)))
    watched = (root / "raw" / "lines.csv", controls.path(root, "lines"), manifest.manifest_path(root / "model.xlsx"))
    before = _files(*watched)
    rep = check.check_model(root, accept_refresh=True, controls={"lines.amount": _total(more)},
                            control_texts=[f"lines.amount={_total(more)}"])
    assert rep.exit_code == 1 and "CT-VERSION" in rules(rep) and "R-ACCEPTED" not in rules(rep), lines(rep)
    assert ("raw_lines: nothing was accepted, because the workbook was built from another version of its contract "
            "(CT-VERSION); raw/lines.csv, raw/lines.controls.json and the manifest were not changed") in rep.notes
    assert _files(*watched) == before


# ============================================================================= 2. [A-aera-1]

def test_criteria_match_rules() -> None:
    m = criteria.excel_criteria_match
    # text that reads as a number equals every text that reads as the same number
    assert m("001", "1") and m("1", "001") and m("1", "1.0") and m("001", 1) and m(1, "001") and m("1,000", "1000")
    assert m("2024-01-31", "45322") and m("1234567890123456", "1234567890123457")      # 15 significant digits
    assert not m("1", "2") and not m("10,00", "1000")
    # case is ignored
    assert m("North", "north") and m("north", "NORTH") and m("Straße", "STRASSE")
    # * and ? are wildcards; ~ escapes them
    assert m("A*", "Apple") and m("A*", "a") and m("A?", "AB") and not m("A?", "A") and not m("A*", "Banana")
    assert m("A~*", "A*") and not m("A~*", "AB") and not m("A*", 10)
    # a criterion that starts with =, < or > is an operator
    assert m("<5", 3) and not m("<5", "3") and not m("<5", 7) and m("=North", "north") and not m("<>North", "north")
    assert m("<>1", "001") and m("=1", "001") and m("=", None) and not m("<>", None)
    # the texts TRUE and FALSE match logical values only
    assert m("TRUE", True) and not m("TRUE", "TRUE")
    # ordinary labels
    for a, b in (("Rent", "Staff"), ("Rent", "Rent "), ("East", "West"), ("P1", "P10"), ("0-30", "1-30"),
                 ("Q1", "Q01"), ("A-1", "A-01"), ("North", "North ")):
        assert not m(a, b), (a, b)
    assert m("Rent", "Rent")


CRITERIA_PAIRS = [
    ("001", "1"), ("1", "001"), ("1", "1.0"), ("01", "1"), ("1", "2"), ("1", 1), (1, "1"), (1, "001"), ("001", 1),
    (" 1", "1"), ("1", " 1"), ("1e0", "1"), ("1,000", "1000"), ("1000", "1,000"), ("10,00", "1000"),
    ("2024-01-31", "45322"), ("2024-01-31", 45322), ("2024-01-31", dt.datetime(2024, 1, 31)), ("1/31/2024", "45322"),
    ("1%", "0.01"), ("$1", "1"), ("(1)", "-1"), (".5", "0.5"), ("1/2", "0.5"), ("12:00", "0.5"),
    ("1234567890123456", "1234567890123457"), ("1.234567890123456", "1.2345678901234567"), ("1", "€1"),
    ("€1", "1"), ("1 ", "1"), ("0", None), (None, 0), ("TRUE", True), ("TRUE", "TRUE"), (True, "TRUE"),
    ("1", True), ("North", "north"), ("Straße", "STRASSE"), ("ß", "ss"), ("?", "ß"), ("S?", "ß"),
    ("A*", "Apple"), ("A*", "a"), ("A?", "AB"), ("A?", "A"), ("A~*", "A*"), ("A~*", "AB"), ("*", "x"),
    ("*", None), ("*", 5), ("1*", "10"), ("1*", 10), ("~", "~"), ("a~b", "ab"), ("~~", "~"), ("*~", "a"),
    ("<5", "3"), ("<5", 3), (">5", 10), ("=North", "north"), ("<>North", "north"), ("<>North", "South"),
    ("=", None), ("<>", None), ("<>", "x"), (">=b", "c"), ("<=b", "B"), ("<b", "B"), ("<>1", "001"), ("=1", "001"),
    ("=001", "1"), (">", "x"), ("=A*", "apple"), ("<>A*", "apple"), ("=<5", "<5"), (">1", "a"), (">a", 5),
    ("<>a", 5), ("<>5", None), ("Rent", "Staff"), ("Rent", "Rent"), ("Rent", "Rent "), ("P1", "P10"),
    ("0-30", "1-30"), ("1-30", "1-30"), ("Jan 2024", "2024-01-01"), ("Q1", "Q01"), ("2026-01", "2026-1"),
]


@pytest.mark.excel
def test_criteria_match_follows_excel_countifs(tmp_path: Path) -> None:
    """Every rule of excel_criteria_match measured against Excel's COUNTIFS: column A holds the cell value, column
    B the criterion, column C =COUNTIFS(A<r>,B<r>)."""
    import openpyxl

    from lib import excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "m"
    for i, (crit, val) in enumerate(CRITERIA_PAIRS, 1):
        for col, v in (("A", val), ("B", crit)):
            if v is not None:
                ws[f"{col}{i}"] = v
                if isinstance(v, str):
                    ws[f"{col}{i}"].data_type = "s"            # a text that starts with = stays text
        ws[f"C{i}"] = f"=COUNTIFS(A{i},B{i})"
    path = tmp_path / "pairs.xlsx"
    wb.save(path)
    n = len(CRITERIA_PAIRS)
    res = excel.recalc(path, read=[f"m!C1:C{n}"])
    assert res.ok, res.errors
    got = [row[0] for row in res.values[f"m!C1:C{n}"]]
    wrong = [(c, v, k, criteria.excel_criteria_match(c, v)) for (c, v), k in zip(CRITERIA_PAIRS, got)
             if bool(k) != criteria.excel_criteria_match(c, v)]
    assert wrong == [], wrong


def _merged_rows() -> list[dict]:
    rows = _rows(STARTER / "data" / "lines.csv")
    return rows + [{"line_id": "L9001", "line_date": "2026-01-15", "category": "001", "amount": "100"},
                   {"line_id": "L9002", "line_date": "2026-01-20", "category": "1", "amount": "-100"}]


CATEGORIES = ["Rent", "Staff", "Supplies", "Travel"]


@pytest.mark.parametrize("case", ["number", "case", "wildcard"])
def test_criteria_findings_of_a_built_workbook(tmp_path: Path, case: str) -> None:
    base = _rows(STARTER / "data" / "lines.csv")
    if case == "number":
        rows, cats = _merged_rows(), ["001", "1"] + CATEGORIES
        want = {("001", "1"), ("1", "001")}
    elif case == "case":
        rows = base + [{"line_id": "L9001", "line_date": "2026-01-15", "category": "rent", "amount": "100"}]
        cats, want = CATEGORIES, {("Rent", "rent")}
    else:
        rows, cats = base, CATEGORIES + ["S*"]
        want = {("S*", "Staff"), ("S*", "Supplies")}
    root = _built_starter(tmp_path, rows=rows, categories=cats)
    wb = root / "model.xlsx"
    found = criteria.criteria_findings(wb, manifest.load_manifest(wb))
    assert {(f.data["label"], f.data["value"]) for f in found} == want, [f.message for f in found]
    reason = {"number": "both read as the number 1", "case": "they differ only in case",
              "wildcard": "'S*' holds the wildcard * or ?"}[case]
    for f in found:
        assert f.rule == "D-CRITERIA" and f.severity == "warning" and f.sheet == "calc"
        assert f.message.startswith(f"block spend (calc!{f.cell}): the label '{f.data['label']}' and the value "
                                    f"'{f.data['value']}' of raw_lines column category (first row ")
        assert f"({reason}), so SUMIFS adds both under '{f.data['label']}'" in f.message
        assert f.data["column"] == "category" and f.data["sheet"] == "raw_lines"
        assert "change the contract's source expression of category" in f.fix and "helper column" in f.fix
    if case == "number":
        first = {f.data["value"]: f.data["row"] for f in found}
        assert first == {"1": 63, "001": 62}               # the raw rows of the two added lines
    # ordinary labels give no warning
    plain = _built_starter(tmp_path / "plain")
    assert criteria.criteria_findings(plain / "model.xlsx", manifest.load_manifest(plain / "model.xlsx")) == []


def test_build_warns_for_criteria_labels_that_excel_merges(tmp_path: Path) -> None:
    """Integration test I-1: needs the integrator's call of criteria.criteria_findings in build.build()."""
    root = starter(tmp_path / "m", rows=_merged_rows(), categories=["001", "1"] + CATEGORIES)
    r = tc.run_cli("build", str(root / "model.spec.json"), "--strict", "--no-power-query",
                   env_extra={"ROTM_NO_EXCEL": "1"})
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "D-CRITERIA" in out, out[-3000:]
    assert "the label '001' and the value '1'" in out and "the label '1' and the value '001'" in out


@pytest.mark.excel
def test_check_warns_for_criteria_labels_that_excel_merges(tmp_path: Path) -> None:
    root = _built_starter(tmp_path, rows=_merged_rows(), categories=["001", "1"] + CATEGORIES)
    r = tc.run_cli("check", str(root), "--strict")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out[-3000:]
    assert "D-CRITERIA" in out and "the label '001' and the value '1'" in out, out[-3000:]
    assert "the label '1' and the value '001'" in out
    rep = check.check_model(root, strict=True)
    assert rep.data["recalculated"] is True and "C-FAIL" not in rules(rep), lines(rep)   # every check holds
    assert {(f.data["label"], f.data["value"]) for f in rep.findings if f.rule == "D-CRITERIA"} == \
        {("001", "1"), ("1", "001")}


def _shipped() -> list[Path]:
    parents = [SKILL / "examples", SKILL / "templates"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        parents.append(PLUGIN / "templates")
    return [d for p in parents if p.is_dir() for d in sorted(p.iterdir()) if (d / "model.xlsx").is_file()]


def test_shipped_models_give_no_criteria_warning(tmp_path: Path) -> None:
    folders = _shipped()
    assert folders
    for folder in folders:
        copy = tmp_path / folder.name
        shutil.copytree(folder, copy, ignore=shutil.ignore_patterns("reports", "lift", "__pycache__"))
        wb = copy / "model.xlsx"
        man = manifest.load_manifest(wb)
        for saved in (False, True):
            found = criteria.criteria_findings(wb, man, saved_values=saved)
            assert found == [], (folder.name, saved, [f.message for f in found])


# ============================================================================= 3. [C-sol-5]

def test_summary_line_counts_cells_without_a_value_as_not_calculated() -> None:
    assert check.summary_line(9, 9) == "checks: 9 of 9 hold"
    assert check.summary_line(0, 9, not_calculated=9) == "checks: not calculated (0 of 9 have a value)"
    assert check.summary_line(3, 5, not_calculated=2) == "checks: 3 of 5 hold, 2 not calculated (no value)"
    assert check.summary_line(4, 4, 1, 2) == "checks: 4 of 4 hold; alerts: 1 of 2 show a value"
    assert check.summary_line(0, 4, 0, 2, not_calculated=4, alerts_not_calculated=2) == \
        "checks: not calculated (0 of 4 have a value); alerts: not calculated (0 of 2 have a value)"
    assert check.summary_line(4, 4, 0, 3, alerts_not_calculated=1) == \
        "checks: 4 of 4 hold; alerts: 0 of 3 show a value, 1 not calculated (no value)"


def test_unrecalculated_workbook_reports_not_calculated(tmp_path: Path) -> None:
    root = _built_starter(tmp_path)
    r = tc.run_cli("check", str(root), "--no-excel", env_extra={"ROTM_NO_EXCEL": "1"})
    out = r.stdout + r.stderr
    assert r.returncode == 3, out[-3000:]
    assert "checks: not calculated (0 of 9 have a value)" in out and "(cached values)" not in out, out[-3000:]
    assert "C-CACHED" not in out
    rec = modelfolder.read_status(root)["workbooks"]["model.xlsx"]["check"]
    assert rec["line"] == "checks: not calculated (0 of 9 have a value)"
    assert rec["alerts"] == 0 and rec["alerts_shown"] is None
    r = tc.run_cli("status", str(root))
    assert "checks: not calculated (0 of 9 have a value)" in r.stdout, r.stdout
    # a model with an alert: the alert cell has no value, so no alert is counted as showing one
    root2 = build_model(tmp_path / "alert")
    assert build.build(root2 / "model.spec.json", strict=True).exit_code == 0
    rep = check.check_model(root2, excel=False)
    n = len([c for c in manifest.load_manifest(root2 / "model.xlsx")["checks"] if c["severity"] == "error"])
    line = f"checks: not calculated (0 of {n} have a value); alerts: not calculated (0 of 1 have a value)"
    assert line in rep.notes and "C-CACHED" not in rules(rep), lines(rep)
    assert modelfolder.check_counts_text(modelfolder.read_status(root2)["workbooks"]["model.xlsx"]["check"]) == line


def test_recalculated_workbook_keeps_the_present_line(tmp_path: Path, monkeypatch) -> None:
    m = tc.make_model(tmp_path / "a", 10)
    fake_excel(monkeypatch, {})                                    # every check cell reads 0 after "recalculation"
    rep = check.check_model(m.root)
    assert "checks: 5 of 5 hold" in rep.notes, rep.notes
    assert modelfolder.read_status(m.root)["workbooks"]["model.xlsx"]["check"]["line"] == "checks: 5 of 5 hold"
    # the shipped starter holds the values Excel saved: without Excel the line is the present one, from those
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    copy = tmp_path / "starter"
    shutil.copytree(STARTER, copy, ignore=shutil.ignore_patterns("reports", "lift", "__pycache__"))
    rep = check.check_model(copy, excel=False)
    assert "checks: 9 of 9 hold (cached values)" in rep.notes, rep.notes


def test_an_old_check_record_keeps_the_present_status_words() -> None:
    assert modelfolder.check_counts_text({"checks": 8, "failed": 1}) == "checks: 7 of 8 hold"
    assert modelfolder.check_counts_text({"checks": 8, "failed": None}) == "checks: 8, not recalculated"
    assert modelfolder.check_counts_text({"checks": 8, "failed": None, "line": "checks: x"}) == "checks: x"


# ============================================================================= 4. supporting [C-sol-3] part 2

def _failed_job(monkeypatch, *, error: str, attached: bool = False, timed_out: bool = False) -> None:
    from lib import excel
    monkeypatch.delenv("ROTM_NO_EXCEL", raising=False)
    monkeypatch.setattr(excel, "excel_status", lambda: SimpleNamespace(available=True, reason=""))

    def job(wb, ops, read_refs, timeout_s=None):
        return excel.JobResult(ok=False, pid=None, errors=[error], attached=attached, timed_out=timed_out), {}, [], 0
    monkeypatch.setattr(check, "_excel_job", job)


OPEN_REASON = "Unable to get the Open property of the Workbooks class"


def _folder_for_root_length(tmp_path: Path, length: int = 150) -> Path:
    """A folder under tmp_path such that make_model's root (<folder>/model) has a path of about `length`
    characters (v5, [A-aera-8, C-sol-8] item 4); tmp_path itself when it is that long already."""
    pad = length - len((tmp_path / "model").as_posix()) - 1
    return tmp_path / ("p" * pad) if pad > 0 else tmp_path


def test_failed_excel_job_is_x_open(tmp_path: Path, monkeypatch) -> None:
    from lib import excel
    m = tc.make_model(tmp_path, 10)
    err = (f"Excel could not open {m.wb.as_posix()}: {OPEN_REASON}")
    _failed_job(monkeypatch, error=err)
    rep = check.check_model(m.root, strict=True)
    assert rep.exit_code == 1, lines(rep)
    x = next(f for f in rep.findings if f.rule == "X-OPEN")
    # v5 ([A-aera-8, C-sol-8] items 3 and 4): check takes the finding from excel.x_open_finding
    expected = excel.x_open_finding(m.wb, [err], m.root)
    assert x.severity == "error" and x.message == expected.message, (x.message, expected.message)
    assert x.fix == expected.fix
    assert x.message.startswith("Excel started but could not open or calculate model.xlsx: ")
    assert x.data["errors"] == [err]
    # v6 (r5 verdict A-aera-6): the fix names the two causes the strict audit finds (F-PARSE, F-ARGS)
    assert x.fix.startswith("the most likely cause is a formula Excel rejects; the strict audit finds two such causes, "
                            "an incomplete formula (F-PARSE) and a listed function with too few or too many arguments "
                            "(F-ARGS): run ")
    assert f"audit \"{m.wb.as_posix()}\" --strict, correct the expression in model.spec.json, rebuild (" in x.fix
    # v6 integrator (request v6_P8 R1): the rebuild between the correction and the check
    assert f"build \"{m.root.as_posix()}\" --force), then run " in x.fix
    assert f"check \"{m.root.as_posix()}\" again" in x.fix
    assert "Excel could not open the workbook; the check cells were not read and nothing was saved" in rep.notes
    assert not any("not recalculated" in line for line in lines(rep)), lines(rep)
    # a timeout is X-OPEN too
    _failed_job(monkeypatch, error="the Excel job did not finish within 600 s and was stopped", timed_out=True)
    assert "X-OPEN" in rules(check.check_model(m.root))
    # Excel handed back an instance this job did not start: as before (not recalculated, exit 3)
    _failed_job(monkeypatch, error="attached to a running Excel", attached=True)
    rep = check.check_model(m.root)
    assert "X-OPEN" not in rules(rep) and rep.exit_code == 3


def test_failed_excel_job_is_x_open_with_a_long_model_path(tmp_path: Path, monkeypatch) -> None:
    """v5 ([A-aera-8, C-sol-8] item 4; I-1: passes with P1's excel.x_open_finding): a model folder whose path is
    about 150 characters; the reason Excel gave stays in the X-OPEN message in full."""
    from lib import excel
    m = tc.make_model(_folder_for_root_length(tmp_path), 10)
    assert len(m.root.as_posix()) >= 150, len(m.root.as_posix())
    err = f"Excel could not open {m.wb.as_posix()}: {OPEN_REASON}"
    _failed_job(monkeypatch, error=err)
    rep = check.check_model(m.root, strict=True)
    assert rep.exit_code == 1, lines(rep)
    x = next(f for f in rep.findings if f.rule == "X-OPEN")
    assert OPEN_REASON in x.message, x.message
    assert x.message.startswith("Excel started but could not open or calculate model.xlsx: ")
    expected = excel.x_open_finding(m.wb, [err], m.root)
    assert x.message == expected.message and x.fix == expected.fix
    assert x.data["errors"] == [err]


@pytest.mark.excel
def test_a_workbook_excel_cannot_open_fails_check_with_x_open(tmp_path: Path) -> None:
    import openpyxl
    root = _built_starter(tmp_path)
    book = openpyxl.load_workbook(root / "model.xlsx")
    book["calc"]["B14"] = "=D10+"                           # a form Excel refuses to open (P1's measurement)
    book.save(root / "model.xlsx")
    r = tc.run_cli("check", str(root), "--strict")
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "X-OPEN" in out, out[-3000:]
    assert "Excel started but could not open or calculate model.xlsx" in out
    assert "not recalculated" not in out


# ============================================================================= 5. supporting [A-aera-8, A-aera-9] part 2

def test_text_key_stored_as_number_fails_check(tmp_path: Path, monkeypatch) -> None:
    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    rows = sales_rows()
    rows[0]["invoice_id"] = "901"
    root = build_model(tmp_path / "m", csv_path=_write_rows(tmp_path / "keys.csv", rows))
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    wb = root / "model.xlsx"
    book = openpyxl.load_workbook(wb)
    assert book["raw_sales"]["A2"].value == "901" and book["raw_sales"]["A2"].data_type == "s"
    book["raw_sales"]["A2"] = 901                                   # the text '901' becomes the number 901
    book.save(wb)
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 1
    f = next(x for x in rep.findings if x.rule == "D-TYPE")
    assert f.severity == "error" and (f.sheet, f.cell) == ("raw_sales", "A2")
    assert f.message == "1 cell(s) of text column invoice_id in raw_sales are stored as numbers (first A2: 901)"
    assert "land" in f.fix and "--force" in f.fix and "rebuild" in f.fix
    assert "R-CHANGED" in rules(rep)                                  # the stored type is part of the digest


# ============================================================================= 6. supporting [C-sol-12, C-sol-13, J-1]

def test_raised_alert_shows_alert_not_fail(tmp_path: Path, monkeypatch) -> None:
    root = build_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    man = manifest.load_manifest(root / "model.xlsx")
    alert = next(c for c in man["checks"] if c["severity"] == "alert")
    fake_excel(monkeypatch, {alert["result"]: 1})
    rep = check.check_model(root)
    f = next(x for x in rep.findings if x.rule == "C-ALERT")
    assert f.severity == "info" and f.message.startswith(f"alert {alert['id']} ({alert['label']}) shows ALERT (1)")
    assert f.message.endswith("; alerts are not part of the master check") and "FAIL" not in f.message

"""Tests of the v8 build changes (package P3 of the v8 plan). Owner: P3.

Entries: [C-sol-4] (a query attached by hand that reads other workbook queries by name: build carries those queries
over as connection-only queries, queries/<name>.hand.pq.m, through the new Excel operation pq_add_query, or names a
query that cannot be made again in the refusal; after Excel ran, the refusal gives only --drop-hand-queries), the build
side of [C-sol-10, J-1] (a label or header written for same_as takes the number format of the cell it links to) and
the build side of [A-aera-5, A-aera-6] part 2 (W-CHECK-ROUND). Tests marked excel drive Excel through lib.excel. The
names of the [C-sol-4] tests are fixed by the v8 plan (section 5.3) and quoted by the documents.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import shutil
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

from lib import build, check, common, excel, land, manifest, styles
from test_v5_build import _shipped_folders, copy_model, read_spec, sheet_of, write_spec
import test_check as tc

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
STARTER = SKILL / "templates" / "starter"


def out(proc) -> str:
    return ((proc.stdout or "") + (proc.stderr or ""))[-8000:]


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def tree_bytes(folder: Path) -> dict[str, bytes]:
    if not folder.is_dir():
        return {}
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def model_state(root: Path) -> dict[str, object]:
    """The workbook, its manifest, raw/ and queries/ byte for byte."""
    return {"workbook": (root / "model.xlsx").read_bytes(), "manifest": (root / "model.manifest.json").read_bytes(),
            "raw": tree_bytes(root / "raw"), "queries": tree_bytes(root / "queries")}


# ============================================================================= [C-sol-4] without Excel

HAND = {"lines": {"sheet": "raw_lines", "old_sheet": "raw_lines", "query_name": "raw_lines"}}


def test_hand_refusal_after_excel_ran_gives_only_drop_hand_queries() -> None:
    ran = build._hand_refusal(HAND, {"lines": "stage_source reads an Excel table of the workbook "
                                              "(Excel.CurrentWorkbook)"}, excel_ran=True, via={"lines": "stage_source"})
    assert ran.message == ("raw_lines is loaded by the query raw_lines attached by hand, which reads the query "
                           "stage_source, and stage_source reads an Excel table of the workbook "
                           "(Excel.CurrentWorkbook); nothing was written")
    assert ran.fix == ("pass --drop-hand-queries to rebuild raw_lines as a static table (the query is removed and must "
                       "be attached by hand again, references/excel-power-query.md \"What a rebuild does to a query "
                       "attached by hand\"); until then the present workbook is kept as it is")
    assert "on a PC with Excel" not in ran.fix
    # Excel did not run: the v7 text
    off = build._hand_refusal(HAND, {"lines": "Excel is not available (ROTM_NO_EXCEL=1)"})
    assert off.message == ("raw_lines is loaded by the query raw_lines attached by hand, and Excel is not available "
                           "(ROTM_NO_EXCEL=1); nothing was written")
    assert off.fix.startswith("run the build on a PC with Excel (build reads the query from the workbook and attaches "
                              "it again), or pass --drop-hand-queries to rebuild raw_lines as a static table")
    # --no-power-query keeps its v7 text, also when Excel ran
    npq = build._hand_refusal(HAND, {"lines": "--no-power-query was given"}, no_power_query=True, excel_ran=True)
    assert npq.fix.startswith("build without --no-power-query on a PC with Excel")


def test_m_names_reads_query_names_outside_quotes_and_fields() -> None:
    reads, binds = build.m_names('let\n    // stage_source in a comment\n    Source = #"stage source",\n'
                                 '    Kept = Table.SelectRows(Source, each [amount] > 0 and [base] <> null),\n'
                                 '    Text = "stage_source /* not a comment */",\n    Joined = base\nin\n    Joined')
    assert "stage source" in reads and "base" in reads and "Table.SelectRows" in reads
    assert "stage_source" not in reads                                 # in a comment and in quotes only
    assert {"Source", "Kept", "Text", "Joined"} <= binds and "Source" not in reads
    reads, _ = build.m_names('let p = Excel.CurrentWorkbook(){[Name="Param"]}[Content]{0}[Value] in p')
    assert "Excel.CurrentWorkbook" in reads and "Content" not in reads and "Value" not in reads


def test_hand_query_reads_orders_dependencies_and_names_what_cannot_be_made_again() -> None:
    f = {"raw_lines": "let Source = stage_source in Source", "stage_source": "let S = base in S",
         "base": 'let x = Csv.Document("a") in x', "unrelated": "1"}
    assert build.hand_query_reads("raw_lines", f) == (["base", "stage_source"], None)
    wb = dict(f, base="let x = Excel.CurrentWorkbook() in x")
    assert build.hand_query_reads("raw_lines", wb) == ([], (
        "stage_source", "stage_source reads the query base, which reads an Excel table of the workbook "
                        "(Excel.CurrentWorkbook)"))
    cyc = dict(f, base="let x = stage_source in x")
    assert build.hand_query_reads("raw_lines", cyc)[1] == (
        "stage_source", "stage_source reads the query base, which reads the query stage_source: the queries read "
                        "each other in a cycle")
    back = dict(f, base="let x = raw_lines in x")
    assert "the queries read each other in a cycle" in build.hand_query_reads("raw_lines", back)[1][1]
    clash = {"raw_lines": "let Source = lines in Source", "lines": "1"}
    # v9 (r8 verdict C-sol-7): the collision text of the v9 plan (5.1), naming both queries when the table's hand
    # query is known
    assert build.hand_query_reads("raw_lines", clash, tables=["lines"])[1] == (
        "lines", "queries/lines.hand.pq.m would be the file of a query attached by hand of the raw table lines (the "
                 "names are equal without regard to case)")
    colon = {"raw_lines": 'let Source = #"a:b" in Source', "a:b": "1"}
    assert "a file name cannot hold the character ':'" in build.hand_query_reads("raw_lines", colon)[1][1]
    unread = {"raw_lines": "let Source = stage in Source", "stage": None}
    assert build.hand_query_reads("raw_lines", unread, workbook="model.xlsx")[1] == (
        "stage", "stage has no M text that could be read from model.xlsx")
    # a self-contained query reads nothing
    assert build.hand_query_reads("raw_lines", {"raw_lines": 'let S = Csv.Document("x") in S', "other": "1"}) == (
        [], None)


def test_pq_add_query_operation_is_normalised() -> None:
    assert "pq_add_query" in excel.EDIT_OPS and "pq_add_query" in excel.OPS
    given = {"op": "pq_add_query", "query_name": "stage source", "m_text": "let x = 1 in x"}
    (op,) = excel.normalize_operations([given])
    assert op == given
    for bad, key in (({"op": "pq_add_query", "m_text": "1"}, "query_name"),
                     ({"op": "pq_add_query", "query_name": "q"}, "m_text")):
        with pytest.raises(common.Refusal) as info:
            excel.normalize_operations([bad])
        assert f"give '{key}'" in info.value.message


def _hand_model_without_excel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The build fixture with its raw sheet turned into a query table of raw_sales (a query attached by hand, made
    in the package as test_v4_build does), without Excel."""
    from test_build import make_model
    from test_v4_build import _make_query_table
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    _make_query_table(root / "model.xlsx", "tbl_raw_sales", "raw_sales")
    return root


def _fake_excel(monkeypatch: pytest.MonkeyPatch, formulas: dict[str, str | None]) -> SimpleNamespace:
    """Excel as available without starting it: excel.inventory gives `formulas` and the raw table at raw_sales!A1;
    pq_attach and pq_add_queries record their calls and succeed; the read-back of the formula gives None."""
    seen = SimpleNamespace(calls=[])
    monkeypatch.delenv("ROTM_NO_EXCEL", raising=False)
    monkeypatch.setattr(excel, "excel_status", lambda: SimpleNamespace(available=True, reason=""))

    def inventory(_wb):
        return {"query_formulas": [{"name": k, "formula": v} for k, v in formulas.items()],
                "sheets": [{"name": "raw_sales", "tables": [{"name": "tbl_raw_sales", "range": "A1:F11", "query": True,
                                                              "connection": "Query - raw_sales"}]}]}

    def ok(**kw):
        return excel.JobResult(ok=True, pid=None)

    def attach(wb, **kw):
        seen.calls.append(("pq_attach", kw["query_name"]))
        return ok()

    def add(wb, queries, *, then=(), **kw):
        seen.calls.append(("pq_add_queries", [q for q, _ in queries], [o["query_name"] for o in then]))
        return ok()
    monkeypatch.setattr(excel, "inventory", inventory)
    monkeypatch.setattr(excel, "pq_attach", attach)
    monkeypatch.setattr(excel, "pq_add_queries", add)
    monkeypatch.setattr(land, "read_query_formula", lambda *a, **k: None)
    return seen


def test_dependencies_are_added_before_the_attach_and_kept_as_files(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    root = _hand_model_without_excel(tmp_path, monkeypatch)
    seen = _fake_excel(monkeypatch, {"raw_sales": "let Source = stage_source in Source",
                                     "stage_source": "let S = base_rows in S",
                                     "base_rows": 'let x = Csv.Document("a") in x'})
    rep = build.build(root / "model.spec.json", force=True)
    assert seen.calls == [("pq_add_queries", ["base_rows", "stage_source"], ["raw_sales"])], seen.calls
    assert (root / "queries" / "stage_source.hand.pq.m").read_text(encoding="utf-8") == "let S = base_rows in S"
    assert (root / "queries" / "base_rows.hand.pq.m").is_file()
    assert ("raw_sales: the query raw_sales attached by hand reads base_rows, stage_source; they were carried over as "
            "connection-only queries (queries/base_rows.hand.pq.m, queries/stage_source.hand.pq.m)") in rep.notes
    assert rep.data["hand_queries_carried"]["sales"]["reads"] == {
        "base_rows": "queries/base_rows.hand.pq.m", "stage_source": "queries/stage_source.hand.pq.m"}
    e = manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")
    assert e["query_attach"] == "hand" and e["query_name"] == "raw_sales"
    # v9 (r8 verdict C-sol-3): the queries read are recorded with the hash of their formula as read back (None here:
    # the stand-in for Excel names no Excel process)
    assert e["query_reads"] == [{"name": "base_rows", "formula_sha256": None, "file": "queries/base_rows.hand.pq.m"},
                                {"name": "stage_source", "formula_sha256": None,
                                 "file": "queries/stage_source.hand.pq.m"}], e


def test_self_contained_hand_query_takes_the_v7_attach(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _hand_model_without_excel(tmp_path, monkeypatch)
    seen = _fake_excel(monkeypatch, {"raw_sales": 'let Source = Csv.Document("a") in Source', "other": "1"})
    rep = build.build(root / "model.spec.json", force=True)
    assert seen.calls == [("pq_attach", "raw_sales")]
    assert not [n for n in rep.notes if "connection-only" in n]
    assert rep.data["hand_queries_carried"]["sales"]["reads"] == {}


def test_dependency_refusal_writes_nothing_without_excel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _hand_model_without_excel(tmp_path, monkeypatch)
    before = model_state(root)
    seen = _fake_excel(monkeypatch, {"raw_sales": "let Source = stage_param in Source",
                                     "stage_param": 'let p = Excel.CurrentWorkbook(){[Name="p"]}[Content] in p'})
    with pytest.raises(common.Refusal) as info:
        build.build(root / "model.spec.json", force=True)
    assert info.value.message == ("raw_sales is loaded by the query raw_sales attached by hand, which reads the query "
                                  "stage_param, and stage_param reads an Excel table of the workbook "
                                  "(Excel.CurrentWorkbook); nothing was written")
    assert "--drop-hand-queries" in info.value.fix and "on a PC with Excel" not in info.value.fix
    assert seen.calls == []
    assert model_state(root) == before
    assert not list(root.glob("*.bak_*")) and not list(root.glob("*.tmp*"))


# ============================================================================= [C-sol-4] with Excel

def _starter_model(base: Path) -> Path:
    """The starter (init --template starter, its contract agreed in a session, the rows landed), built without
    Power Query; data/lines.csv holds the rows that were landed."""
    from test_v4_check import _built_starter
    root = _built_starter(base)
    src = root / "data" / "lines.csv"
    if not src.is_file():
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STARTER / "data" / "lines.csv", src)
    return root


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _total(rows: list[dict]) -> Decimal:
    return sum((Decimal(r["amount"]) for r in rows), Decimal(0))


def _stage_text(root: Path, *, reads_workbook: bool = False) -> str:
    """The staging query: the contract's typed query over data/lines.csv (land.m_text). With `reads_workbook`, one
    step reads Excel.CurrentWorkbook() (M evaluates it only when a later step uses it, so the query still loads)."""
    c = common.read_json(root / "contracts" / "lines.contract.json")
    text = land.m_text(c, csv_path=root / "data" / "lines.csv")
    if reads_workbook:
        text = text.replace("let\n", "let\n    Book = Excel.CurrentWorkbook(),\n", 1)
    return text


RAW_TEXT = "let\n    Source = stage_source\nin\n    Source"


def _attach_by_hand(root: Path, stage: str) -> None:
    """As a user does in Excel: the staging query stage_source (connection-only; on v7, which has no such operation,
    loaded to a scratch sheet), then the query raw_lines = stage_source loaded as tbl_raw_lines at raw_lines!A1."""
    wb = root / "model.xlsx"
    attach = {"op": "pq_attach", "sheet": "raw_lines", "table_name": "tbl_raw_lines", "query_name": "raw_lines",
              "m_text": RAW_TEXT}
    if hasattr(excel, "pq_add_queries"):
        res = excel.pq_add_queries(wb, [("stage_source", stage)], then=[attach])
    else:
        res = excel.run_job(wb, [{"op": "add_sheet", "name": "scratch"},
                                 {"op": "pq_attach", "sheet": "scratch", "table_name": "tbl_stage",
                                  "query_name": "stage_source", "m_text": stage}, attach], save=True)
    assert res.ok, res.message()
    assert check.query_tables(wb).get("raw_lines") == "raw_lines"


def _calc_total(root: Path) -> float:
    wb = root / "model.xlsx"
    m = manifest.load_manifest(wb)
    cell = manifest.sheet_entry(m, "calc")["blocks"][0]["total"]
    return float(openpyxl.load_workbook(wb, data_only=True)["calc"][cell].value)


@pytest.mark.excel
def test_pq_add_queries_adds_a_connection_only_query(tmp_path: Path) -> None:
    root = _starter_model(tmp_path)
    wb = root / "model.xlsx"
    before = excel.inventory(wb)
    stage = _stage_text(root)
    res = excel.pq_add_queries(wb, [("stage_source", stage)])
    assert res.ok, res.message()
    assert [s.get("query") for s in res.steps if s.get("op") == "pq_add_query"] == ["added"]
    inv = excel.inventory(wb)
    formulas = {q["name"]: q["formula"] for q in inv["query_formulas"]}
    assert formulas["stage_source"].replace("\r\n", "\n").rstrip() == stage.rstrip()

    def tables(i: dict) -> list[str]:
        return sorted(t["name"] for s in i["sheets"] for t in s.get("tables") or [])
    assert tables(inv) == tables(before)                                       # no new table: nothing was loaded
    assert [s["name"] for s in inv["sheets"]] == [s["name"] for s in before["sheets"]]
    assert check.query_tables(wb) == {}
    # the same name again: its formula is set
    res = excel.pq_add_queries(wb, [("stage_source", stage.replace("Typed", "Typed2"))])
    assert res.ok and [s.get("query") for s in res.steps if s.get("op") == "pq_add_query"] == ["updated"]


@pytest.mark.excel
def test_rebuild_carries_a_staging_query_a_hand_query_reads(outside_tmp: Path) -> None:
    root = _starter_model(outside_tmp)
    wb = root / "model.xlsx"
    stage = _stage_text(root)
    _attach_by_hand(root, stage)
    rows = _rows(root / "data" / "lines.csv")
    acc = tc.run_cli("check", str(root), "--accept-refresh", "--strict", "--control", f"lines.amount={_total(rows)}")
    assert acc.returncode == 0, out(acc)
    e = manifest.raw_sheet(manifest.load_manifest(wb), "lines")
    assert e["query_attach"] == "hand" and e["query_name"] == "raw_lines", e

    reb = tc.run_cli("build", str(root), "--strict", "--force")
    assert reb.returncode == 0, out(reb)
    text = out(reb)
    assert ("raw_lines: the query raw_lines attached by hand reads stage_source; it was carried over as a "
            "connection-only query (queries/stage_source.hand.pq.m)") in text
    kept = root / "queries" / "stage_source.hand.pq.m"
    assert kept.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() == stage.rstrip()
    assert (root / "queries" / "lines.hand.pq.m").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() == \
        RAW_TEXT
    inv = excel.inventory(wb)
    assert {q["name"] for q in inv["query_formulas"]} == {"stage_source", "raw_lines"}
    loaded = sorted((s["name"], t["name"]) for s in inv["sheets"] for t in s.get("tables") or [] if t.get("query"))
    assert loaded == [("raw_lines", "tbl_raw_lines")]                         # stage_source: connection-only
    assert check.query_tables(wb) == {"raw_lines": "raw_lines"}

    # a change of the source reaches an output cell through both queries
    rows[0]["amount"] = str(Decimal(rows[0]["amount"]) + Decimal(1000))
    _write_rows(root / "data" / "lines.csv", rows)
    ref = tc.run_cli("check", str(root), "--refresh", "--strict")
    assert "refreshed raw_lines" in out(ref), out(ref)
    acc = tc.run_cli("check", str(root), "--accept-refresh", "--strict", "--control", f"lines.amount={_total(rows)}")
    assert acc.returncode == 0, out(acc)
    chk = tc.run_cli("check", str(root), "--strict")
    assert chk.returncode == 0, out(chk)
    assert abs(_calc_total(root) - float(_total(rows))) < 1e-6


@pytest.mark.excel
def test_self_contained_hand_query_is_carried_as_in_v7(outside_tmp: Path) -> None:
    root = _starter_model(outside_tmp)
    wb = root / "model.xlsx"
    m = _stage_text(root)
    res = excel.pq_attach(wb, sheet="raw_lines", table_name="tbl_raw_lines", query_name="q_by_hand", m_text=m)
    assert res.ok, res.message()
    reb = tc.run_cli("build", str(root), "--strict", "--force")
    assert reb.returncode == 0, out(reb)
    text = out(reb)
    assert ("raw_lines: the query q_by_hand attached by hand was carried over: its M text is kept in "
            "queries/lines.hand.pq.m and it was attached again with the same name") in text
    assert "connection-only" not in text
    assert sorted(p.name for p in (root / "queries").glob("*.hand.pq.m")) == ["lines.hand.pq.m"]
    assert check.query_tables(wb) == {"raw_lines": "q_by_hand"}
    e = manifest.raw_sheet(manifest.load_manifest(wb), "lines")
    assert e["query_attach"] == "hand" and e["query_file"] == "queries/lines.hand.pq.m"


@pytest.mark.excel
def test_dependency_that_reads_the_workbook_is_named_in_the_refusal(outside_tmp: Path) -> None:
    root = _starter_model(outside_tmp)
    _attach_by_hand(root, _stage_text(root, reads_workbook=True))
    before = model_state(root)
    status = (root / "reports" / "status.json").read_bytes()
    reb = tc.run_cli("build", str(root), "--strict", "--force")
    assert reb.returncode == 2, out(reb)
    text = out(reb)
    assert ("raw_lines is loaded by the query raw_lines attached by hand, which reads the query stage_source, and "
            "stage_source reads an Excel table of the workbook (Excel.CurrentWorkbook); nothing was written") in text
    assert "--drop-hand-queries" in text and "until then the present workbook is kept as it is" in text
    assert "on a PC with Excel" not in text
    assert model_state(root) == before
    assert (root / "reports" / "status.json").read_bytes() == status
    assert not list(root.glob("*.bak_*")) and not list(root.glob("*.tmp*"))


# ============================================================================= [C-sol-10, J-1] build side

DATES = ["2026-01-31", "2026-02-28", "2026-03-31"]


def _date_blocks(data: dict) -> None:
    """The starter with an input list of dates: block by_month (rows from the list) and by_month_copy (rows
    same_as by_month); block month_grid (cols from the list) and month_grid_copy (rows and cols same_as
    month_grid); each block is read by a check."""
    sheet_of(data, "inputs")["items"].append({"id": "months", "kind": "list", "label": "Months", "values": DATES,
                                              "format": "date", "source": "test"})
    sheet_of(data, "calc")["blocks"] += [
        {"id": "by_month", "title": "Expenses by month", "unit": "KRW", "format": "amount",
         "rows": {"in": "months", "label": "Month"},
         "cell": "SUMIFS({h:lines.amount},{h:lines.month_end},{row})", "total": {"row": True}},
        {"id": "by_month_copy", "title": "Expenses by month, linked", "unit": "KRW", "format": "amount",
         "rows": {"same_as": "by_month", "label": "Month"}, "cols": {"same_as": "by_month"},
         "cell": "{g:by_month}", "total": {"row": True}},
        {"id": "month_grid", "title": "Expenses by category and listed month", "unit": "KRW", "format": "amount",
         "rows": {"in": "categories", "label": "Category"}, "cols": {"in": "months"},
         "cell": "SUMIFS({h:lines.amount},{h:lines.category},{row},{h:lines.month_end},{col})",
         "total": {"row": True, "col": True}},
        {"id": "month_grid_copy", "title": "Expenses by category and listed month, linked", "unit": "KRW",
         "format": "amount", "rows": {"same_as": "month_grid", "label": "Category"},
         "cols": {"same_as": "month_grid"}, "cell": "{g:month_grid}", "total": {"row": True, "col": True}}]
    sheet_of(data, "checks")["checks"] += [
        {"id": "by_month_ties", "kind": "equal", "label": "Month block total equals the raw total",
         "actual": "{blk:by_month.total}", "expected": "SUM({raw:lines.amount})"},
        {"id": "by_month_copy_ties", "kind": "equal", "label": "Linked month block equals the month block",
         "actual": "{blk:by_month_copy.total}", "expected": "{blk:by_month.total}"},
        {"id": "month_grid_ties", "kind": "equal", "label": "Listed-month grid total equals the raw total",
         "actual": "{blk:month_grid.total}", "expected": "SUM({raw:lines.amount})"},
        {"id": "month_grid_copy_ties", "kind": "equal", "label": "Linked grid equals the listed-month grid",
         "actual": "{blk:month_grid_copy.total}", "expected": "{blk:month_grid.total}"}]


def _date_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, excel_on: bool = False) -> Path:
    if not excel_on:
        monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = copy_model(STARTER, tmp_path / "dates", monkeypatch)
    data = read_spec(root)
    _date_blocks(data)
    write_spec(root, data)
    rep = build.build(root / "model.spec.json", strict=True, force=True, power_query=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    return root


def _block(m: dict, bid: str) -> dict:
    return next(b for s in m["sheets"] for b in s.get("blocks") or [] if b["id"] == bid)


def _label_cells(b: dict) -> list[str]:
    if not b.get("labels"):
        return []
    c1, r1, _c2, r2 = common.range_bounds(b["labels"])
    return [common.a1(c1, r) for r in range(r1, r2 + 1)]


def _header_cells(b: dict) -> list[str]:
    if not b.get("body") or not b.get("header_row"):
        return []
    c1, _r1, c2, _r2 = common.range_bounds(b["body"])
    return [common.a1(c, int(b["header_row"])) for c in range(c1, c2 + 1)]


def test_same_as_rows_of_a_date_list_show_dates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _date_model(tmp_path, monkeypatch)
    m = manifest.load_manifest(root / "model.xlsx")
    ws = openpyxl.load_workbook(root / "model.xlsx")["calc"]
    src, linked = _block(m, "by_month"), _block(m, "by_month_copy")
    assert linked["rows_kind"] == "same_as"
    pairs = list(zip(_label_cells(src), _label_cells(linked)))
    assert len(pairs) == 3
    for a, b in pairs:
        assert ws[a].number_format == "yyyy-mm-dd", (a, ws[a].number_format)
        assert ws[b].number_format == ws[a].number_format, (b, ws[b].number_format)
        assert ws[b].value == f"=$B{a[1:]}", ws[b].value
    # the rows of month_grid_copy link to a text list: General, as in v7
    for b in _label_cells(_block(m, "month_grid_copy")):
        assert ws[b].number_format == "General", (b, ws[b].number_format)


def test_same_as_cols_of_a_date_list_show_dates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _date_model(tmp_path, monkeypatch)
    m = manifest.load_manifest(root / "model.xlsx")
    ws = openpyxl.load_workbook(root / "model.xlsx")["calc"]
    src, linked = _block(m, "month_grid"), _block(m, "month_grid_copy")
    assert linked["cols_kind"] == "same_as"
    pairs = list(zip(_header_cells(src), _header_cells(linked)))
    assert len(pairs) == 3
    for a, b in pairs:
        assert ws[a].number_format == "yyyy-mm-dd", (a, ws[a].number_format)
        assert ws[b].number_format == ws[a].number_format, (b, ws[b].number_format)
        assert ws[b].value == f"={a}", ws[b].value


@pytest.mark.excel
def test_same_as_labels_of_a_date_list_show_dates_in_excel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After check --strict (exit 0) the linked labels and headers hold the date serials and show dates; audit
    --strict exits 0 (it needs P4's S-NUMFMT side, v8 plan section 7 I-3)."""
    root = _date_model(tmp_path, monkeypatch, excel_on=True)
    chk = tc.run_cli("check", str(root), "--strict")
    assert chk.returncode == 0, out(chk)
    m = manifest.load_manifest(root / "model.xlsx")
    ws = openpyxl.load_workbook(root / "model.xlsx", data_only=True)["calc"]
    serials = [excel.excel_serial(dt.date.fromisoformat(d)) for d in DATES]
    for cells in (_label_cells(_block(m, "by_month_copy")), _header_cells(_block(m, "month_grid_copy"))):
        got = []
        for c in cells:
            v = ws[c].value
            got.append(excel.excel_serial(v) if hasattr(v, "year") else float(v))
            assert styles.number_formats_equal(ws[c].number_format, "yyyy-mm-dd"), (c, ws[c].number_format)
        assert got == serials, (cells, got)
    aud = tc.run_cli("audit", str(root), "--strict")
    assert aud.returncode == 0, out(aud)


def _shipped_model_copy(folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if not (folder / "model.spec.json").is_file() or not (folder / "model.xlsx").is_file():
        pytest.skip(f"{folder.name} is not in this copy (the skill folder copied alone)")
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    return copy_model(folder, tmp_path / folder.name, monkeypatch)


@pytest.mark.parametrize("folder", _shipped_folders(), ids=lambda p: p.name)
def test_shipped_models_keep_their_label_formats(folder: Path, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Each shipped specification built again: for every block present in the new build and in the shipped
    model.xlsx, the number formats of its row labels and value column headers, found by block id and position from
    the two manifests, are equal (styles.number_formats_equal: the shipped workbooks were saved by Excel)."""
    root = _shipped_model_copy(folder, tmp_path, monkeypatch)
    rep = build.build(root / "model.spec.json", force=True)
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    old_m = manifest.load_manifest(folder / "model.xlsx")
    new_m = manifest.load_manifest(root / "model.xlsx")
    old_wb = openpyxl.load_workbook(folder / "model.xlsx")
    new_wb = openpyxl.load_workbook(root / "model.xlsx")
    compared = 0
    for s in new_m["sheets"]:
        for nb in s.get("blocks") or []:
            try:
                ob = next(b for os_ in old_m["sheets"] if os_["name"] == s["name"]
                          for b in os_.get("blocks") or [] if b["id"] == nb["id"])
            except StopIteration:
                continue                                                      # a block the shipped build lacks
            for cells in (_label_cells, _header_cells):
                for oc, nc in zip(cells(ob), cells(nb)):
                    new, old = new_wb[s["name"]][nc].number_format, old_wb[s["name"]][oc].number_format
                    # Excel rewrites a format when it saves (yyyy-mm is stored as yyyy\-mm): the audit's comparison
                    assert styles.number_formats_equal(new, old), (s["name"], nb["id"], oc, nc, new, old)
                    compared += 1
    assert compared > 0


# ============================================================================= W-CHECK-ROUND (build side)

def _starter_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change) -> Path:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = copy_model(STARTER, tmp_path / "starter", monkeypatch)
    data = read_spec(root)
    change(data)
    write_spec(root, data)
    return root


def _rounded(data: dict) -> None:
    sheet_of(data, "checks")["checks"] += [
        {"id": "rounded", "kind": "equal", "label": "Grid total equals the raw total, both rounded",
         "actual": "ROUND({blk:spend.total},0)", "expected": "ROUND(SUM({raw:lines.amount}),0)"},
        {"id": "rounded_int", "kind": "zero", "label": "Whole part of the difference of the two totals",
         "actual": "INT({blk:spend.total}-SUM({raw:lines.amount}))"},
        {"id": "rounded_floor", "kind": "range", "label": "Grid total between the floored and the raw totals",
         "actual": "{blk:spend.total}", "min": "FLOOR.MATH(SUM({raw:lines.amount}))",
         "max": "SUM({raw:lines.amount})"}]


def test_check_with_round_gets_w_check_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _starter_with(tmp_path, monkeypatch, _rounded)
    data = read_spec(root)
    ci = data["sheets"].index(sheet_of(data, "checks"))
    n = len(sheet_of(data, "checks")["checks"])
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert rep.exit_code == 1                                                 # strict: the warning fails the build
    got = {(f.data["check"], f.data["field"], f.data["function"]): f
           for f in rep.findings if f.rule == "W-CHECK-ROUND"}
    assert set(got) == {("rounded", "actual", "ROUND"), ("rounded", "expected", "ROUND"),
                        ("rounded_int", "actual", "INT"), ("rounded_floor", "min", "FLOOR.MATH")}, got
    f = got[("rounded", "actual", "ROUND")]
    path = f"sheets[{ci}].checks[{n - 3}].actual"
    assert f.severity == "warning" and f.sheet == "checks" and f.cell.startswith("D")
    assert f.message == (f"check rounded: its actual expression holds ROUND ({path}); references/checks.md rule 3: a "
                         "check compares the unrounded difference with the tolerance")
    assert f.fix == ("remove ROUND from the check and compare the unrounded values with the tolerance; when a business "
                     "rule needs rounded values, round them in a block and tie that block to an independent total")
    assert got[("rounded", "expected", "ROUND")].cell.startswith("E")
    assert f"checks[{n - 1}].min" in got[("rounded_floor", "min", "FLOOR.MATH")].message
    cli = tc.run_cli("build", str(root), "--strict", "--force")
    assert cli.returncode == 1 and "W-CHECK-ROUND" in out(cli), out(cli)
    assert build.CHECK_ROUND_FUNCTIONS == ("ROUND", "ROUNDUP", "ROUNDDOWN", "MROUND", "INT", "TRUNC", "CEILING",
                                           "FLOOR", "CEILING.MATH", "CEILING.PRECISE", "ISO.CEILING", "FLOOR.MATH",
                                           "FLOOR.PRECISE")


def test_function_name_in_quotes_is_not_w_check_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def quoted(data: dict) -> None:
        sheet_of(data, "checks")["checks"].append(
            {"id": "quoted", "kind": "equal", "label": "Grid total against the raw total, with a text that names ROUND",
             "actual": "{blk:spend.total}*--(LEN(\"ROUND(INT(x),0)\")>0)", "expected": "SUM({raw:lines.amount})"})
    root = _starter_with(tmp_path, monkeypatch, quoted)
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert not [f.line() for f in rep.findings if f.rule == "W-CHECK-ROUND"]
    assert rep.exit_code == 0, [f.line() for f in rep.findings]


@pytest.mark.parametrize("folder", _shipped_folders(), ids=lambda p: p.name)
def test_shipped_models_get_no_w_check_round(folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _shipped_model_copy(folder, tmp_path, monkeypatch)
    rep = build.build(root / "model.spec.json", force=True)
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    assert not [f.line() for f in rep.findings if f.rule == "W-CHECK-ROUND"]

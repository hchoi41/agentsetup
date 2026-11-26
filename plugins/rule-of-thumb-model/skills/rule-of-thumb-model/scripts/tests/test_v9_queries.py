"""Tests of the v9 entry [C-sol-3, C-sol-7] (package P2 of the v9 plan): the queries that a query attached by hand
reads. Owner: P2.

- (1) C-sol-7: build keeps the raw table names apart from the loop's list of query tables, so they reach
  _dep_file_problem. A query read whose file queries/<name>.hand.pq.m would be the file of a raw table's query
  attached by hand, or of another query read (the names are equal without regard to case), is refused (B-REFUSED,
  exit 2) naming both queries; the workbook, the manifest and queries/ stay byte for byte. Tested with a stand-in for
  lib.excel.inventory (the helpers of test_v8_build.py).
- (2) C-sol-3, full form: build records query_reads (name, formula_sha256 of the formula read back, file) in the raw
  entry; check --refresh and check --accept-refresh compare each one: a changed, missing or unreadable one is
  X-QUERY-CHANGED naming it; the table is not refreshed, nothing is accepted, raw/<t>.csv, raw/<t>.controls.json and
  the manifest stay byte for byte.

The names of the two Excel tests are fixed by the v9 plan (section 5.3) and quoted by the documents.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from lib import build, check, common, excel, land, manifest
import test_check as tc
from test_v8_build import (RAW_TEXT, _attach_by_hand, _fake_excel, _hand_model_without_excel, _rows, _stage_text,
                           _starter_model, _total, _write_rows, model_state, out)


def three_files(root: Path, table: str) -> dict[str, bytes | None]:
    paths = [root / "raw" / f"{table}.csv", root / "raw" / f"{table}.controls.json", root / "model.manifest.json"]
    return {p.name: (p.read_bytes() if p.is_file() else None) for p in paths}


# ============================================================================= (1) C-sol-7, without Excel

def test_staging_query_named_like_its_raw_table_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The loaded query raw_sales reads a staging query named sales, the name of its raw table: its file
    queries/sales.hand.pq.m would be the file of raw_sales. v8 wrote the loaded query's text and then the staging
    query's text to that one file (the manifest named it for the loaded query). v9 refuses (exit 2) before anything
    is written."""
    root = _hand_model_without_excel(tmp_path, monkeypatch)
    before = model_state(root)
    seen = _fake_excel(monkeypatch, {"raw_sales": "let Source = sales in Source",
                                     "sales": 'let x = Csv.Document("a") in x'})
    with pytest.raises(common.Refusal) as info:
        build.build(root / "model.spec.json", force=True)
    assert info.value.message == (
        "raw_sales is loaded by the query raw_sales attached by hand, which reads the query sales; "
        "queries/sales.hand.pq.m would be the file of the query raw_sales attached by hand of the raw table sales "
        "(the names are equal without regard to case); nothing was written")
    assert info.value.fix.startswith("rename one of the two queries in Excel (Data > Queries & Connections), or pass "
                                     "--drop-hand-queries to rebuild raw_sales as a static table")
    assert seen.calls == []
    assert model_state(root) == before
    assert not list(root.glob("*.bak_*")) and not list(root.glob("*.tmp*"))


def test_staging_query_named_like_the_raw_table_in_another_case_is_refused(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    root = _hand_model_without_excel(tmp_path, monkeypatch)
    before = model_state(root)
    _fake_excel(monkeypatch, {"raw_sales": "let Source = Sales in Source", "Sales": 'let x = Csv.Document("a") in x'})
    with pytest.raises(common.Refusal) as info:
        build.build(root / "model.spec.json", force=True)
    assert "which reads the query Sales; queries/Sales.hand.pq.m would be the file of the query raw_sales attached " \
           "by hand of the raw table sales (the names are equal without regard to case)" in info.value.message
    assert model_state(root) == before


def test_two_dependencies_differing_only_in_case_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two queries read whose names differ only in case would share one file on Windows: refused naming both."""
    root = _hand_model_without_excel(tmp_path, monkeypatch)
    before = model_state(root)
    seen = _fake_excel(monkeypatch, {"raw_sales": "let a = Stage, b = stage in Table.Combine({a, b})",
                                     "Stage": 'let x = Csv.Document("a") in x',
                                     "stage": 'let y = Csv.Document("b") in y'})
    with pytest.raises(common.Refusal) as info:
        build.build(root / "model.spec.json", force=True)
    assert info.value.message == (
        "raw_sales is loaded by the query raw_sales attached by hand, which reads the query stage; "
        "queries/stage.hand.pq.m would be the file of the query Stage, which raw_sales also reads (the names are "
        "equal without regard to case); nothing was written")
    assert info.value.fix.startswith("rename one of the two queries in Excel")
    assert seen.calls == []
    assert model_state(root) == before


def test_raw_table_names_reach_the_file_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_read_hand_queries passes the raw table names it was given (not the list of query tables of the loop)."""
    got: list[list[str]] = []
    real = build.hand_query_reads

    def spy(name, formulas, **kw):
        got.append(list(kw.get("tables") or []))
        return real(name, formulas, **kw)
    root = _hand_model_without_excel(tmp_path, monkeypatch)
    _fake_excel(monkeypatch, {"raw_sales": 'let Source = Csv.Document("a") in Source'})
    monkeypatch.setattr(build, "hand_query_reads", spy)
    build.build(root / "model.spec.json", force=True)
    assert got == [["sales"]], got


def test_other_dependency_names_are_carried_as_in_v8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _hand_model_without_excel(tmp_path, monkeypatch)
    seen = _fake_excel(monkeypatch, {"raw_sales": "let Source = stage_sales in Source",
                                     "stage_sales": 'let x = Csv.Document("a") in x'})
    build.build(root / "model.spec.json", force=True)
    assert seen.calls == [("pq_add_queries", ["stage_sales"], ["raw_sales"])]
    assert (root / "queries" / "sales.hand.pq.m").read_text(encoding="utf-8") == "let Source = stage_sales in Source"
    assert (root / "queries" / "stage_sales.hand.pq.m").read_text(encoding="utf-8") == 'let x = Csv.Document("a") in x'


# ============================================================================= (2) C-sol-3, without Excel

def _entry(recorded: str) -> dict:
    return {"name": "raw_lines", "table": "lines", "landing": "power_query", "query_attach": "hand",
            "query_name": "raw_lines", "query_reads": [{"name": "stage_source", "formula_sha256": recorded,
                                                        "file": "queries/stage_source.hand.pq.m"}]}


def test_query_read_findings_name_a_changed_missing_or_unreadable_query(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """check._query_formula_findings with a stand-in for lib.excel.inventory: the four cases of a query read."""
    stage = "let\n    S = 1\nin\n    S"
    recorded = land.query_formula_sha256(stage)
    entries = {"lines": _entry(recorded)}
    qtables = {"raw_lines": "raw_lines"}
    assert check._formula_tables(entries, qtables) == ["lines"]          # no query_formula_sha256: the reads only

    def run(formulas: dict | None, accept: bool = True) -> list:
        inv = {} if formulas is None else {"query_formulas": [{"name": k, "formula": v} for k, v in formulas.items()]}
        monkeypatch.setattr(excel, "inventory", lambda _wb: inv)
        return check._query_formula_findings(tmp_path, tmp_path / "model.xlsx", entries, qtables, ["lines"], accept)
    assert run({"raw_lines": RAW_TEXT, "stage_source": stage + "\r\n"}) == []          # line ends made uniform
    changed = stage.replace("S = 1", "S = 2")
    (f,) = run({"raw_lines": RAW_TEXT, "stage_source": changed})
    now = land.query_formula_sha256(changed)
    assert f.rule == "X-QUERY-CHANGED" and f.severity == "error" and f.sheet == "raw_lines"
    assert f.message == ("raw_lines: the query stage_source, which the query raw_lines attached by hand reads, differs "
                         f"in the workbook from the formula build recorded (manifest query_reads sha256:{recorded[:12]}"
                         f"..., the workbook now sha256:{now[:12]}...); the table was not refreshed and nothing was "
                         "accepted")
    assert f.fix.startswith("review the change of stage_source with the user; to keep it, rebuild (")
    assert f"build \"{tmp_path.as_posix()}\" --force), which records it, then refresh and accept; to undo it, put " \
           "back its M text from queries/stage_source.hand.pq.m" in f.fix
    assert f.data["query"] == "stage_source" and f.data["read_by"] == "raw_lines"
    assert run({"raw_lines": RAW_TEXT, "stage_source": changed}, accept=False)[0].severity == "warning"
    (f,) = run({"raw_lines": RAW_TEXT})
    assert "the query stage_source, which the query raw_lines attached by hand reads, is missing from the workbook" \
           in f.message
    (f,) = run({"raw_lines": RAW_TEXT, "stage_source": None})
    assert "attached by hand reads, cannot be read; the table" in f.message
    (f,) = run(None)
    assert "cannot be read" in f.message
    # a hash build could not read back: not compared
    entries["lines"] = _entry(None)
    assert run({"raw_lines": RAW_TEXT, "stage_source": changed}) == []


# ============================================================================= (2) C-sol-3, with Excel (fixed names)

def _carried(root: Path) -> tuple[str, list[dict]]:
    """The starter with raw_lines loaded by the query raw_lines attached by hand, which reads the connection-only
    staging query stage_source; accepted, then rebuilt (build carries both over). Returns (staging text, rows)."""
    stage = _stage_text(root)
    _attach_by_hand(root, stage)
    rows = _rows(root / "data" / "lines.csv")
    acc = tc.run_cli("check", str(root), "--accept-refresh", "--strict", "--control", f"lines.amount={_total(rows)}")
    assert acc.returncode == 0, out(acc)
    reb = tc.run_cli("build", str(root), "--strict", "--force")
    assert reb.returncode == 0, out(reb)
    e = manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "lines")
    inv = {q["name"]: q["formula"] for q in excel.inventory(root / "model.xlsx")["query_formulas"]}
    assert e["query_reads"] == [{"name": "stage_source",
                                 "formula_sha256": land.query_formula_sha256(inv["stage_source"]),
                                 "file": "queries/stage_source.hand.pq.m"}], e
    return stage, rows


@pytest.mark.excel
def test_changed_staging_query_is_x_query_changed_and_nothing_is_accepted(outside_tmp: Path) -> None:
    """Only the staging query's formula changed through Excel (excel.pq_add_queries with its name sets its formula):
    check --refresh --accept-refresh --strict does not exit 0, X-QUERY-CHANGED names stage_source, and raw/lines.csv,
    raw/lines.controls.json and the manifest stay byte for byte. v8 compared the loaded query only and refreshed."""
    root = _starter_model(outside_tmp)
    stage, rows = _carried(root)
    changed = stage.replace("Typed", "Typed2")
    assert changed != stage
    res = excel.pq_add_queries(root / "model.xlsx", [("stage_source", changed)])
    assert res.ok and [s.get("query") for s in res.steps if s.get("op") == "pq_add_query"] == ["updated"]
    rows[0]["amount"] = str(Decimal(rows[0]["amount"]) + Decimal(1000))    # a refresh would bring other rows
    _write_rows(root / "data" / "lines.csv", rows)
    before = three_files(root, "lines")
    r = tc.run_cli("check", str(root), "--refresh", "--accept-refresh", "--strict")
    assert r.returncode != 0, out(r)
    text = out(r)
    assert "X-QUERY-CHANGED" in text and ("raw_lines: the query stage_source, which the query raw_lines attached by "
                                          "hand reads, differs in the workbook") in text, text
    assert "refreshed raw_lines" not in text and "R-ACCEPTED" not in text
    assert three_files(root, "lines") == before


@pytest.mark.excel
def test_unchanged_carried_queries_pass_the_refresh_cycle(outside_tmp: Path) -> None:
    """With unchanged queries the refresh-and-accept cycle passes: a source change reaches the workbook through both
    queries, check --accept-refresh --control and check --strict exit 0, and no X-QUERY-CHANGED is reported."""
    root = _starter_model(outside_tmp)
    _stage, rows = _carried(root)
    rows[0]["amount"] = str(Decimal(rows[0]["amount"]) + Decimal(1000))
    _write_rows(root / "data" / "lines.csv", rows)
    ref = tc.run_cli("check", str(root), "--refresh", "--strict")
    assert "refreshed raw_lines" in out(ref) and "X-QUERY-CHANGED" not in out(ref), out(ref)
    acc = tc.run_cli("check", str(root), "--accept-refresh", "--strict", "--control", f"lines.amount={_total(rows)}")
    assert acc.returncode == 0 and "X-QUERY-CHANGED" not in out(acc), out(acc)
    chk = tc.run_cli("check", str(root), "--strict")
    assert chk.returncode == 0, out(chk)

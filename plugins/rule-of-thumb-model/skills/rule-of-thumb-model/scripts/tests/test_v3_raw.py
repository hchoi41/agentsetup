"""Tests of the v3 raw-layer changes (package P2 of the v3 plan). Owner: P2.

Entries: [A-aera-1, A-aera-4, C-sol-1] (control totals, contract version, acceptance only after a refresh that ran,
the workbook digest), [A-aera-2] (owned hashes follow the chain of writes), [J-1] (checks columns sized from
their contents), [A-aera-5, C-sol-2] (check --refresh refused before Excel starts), [C-sol-7] (a query attached
by hand), [A-aera-8, C-sol-6] (a raw table with zero rows), [A-aera-10] (numbers stored as text), [A-aera-19]
(W-SELF-CHECK), [C-sol-5] (D-KEY-SEPARATOR), [J-2] (header cells of an input list), supporting [A-aera-12, C-sol-9,
C-sol-10] part 5 (X-NO-QUERY) and [A-aera-15, A-aera-17] (the V-NO-VALUE fixes of check). Tests marked excel drive
Excel through lib.excel. Integration tests I-1 to I-4 (plan section 7) need changes of other packages.
"""
from __future__ import annotations

import csv
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from lib import audit, build, check, common, contract, controls, land, manifest, modelfolder, spec as specmod
from test_build import base_contract, base_spec, load, make_model, refusal_lines, sales_rows
from test_v2_raw_layer import _rows_only_contract, _sheet, _write_rows, fake_excel
import test_check as tc

SKILL = Path(__file__).resolve().parents[2]
PLUGIN = SKILL.parents[1]


def shipped_models() -> list[Path]:
    """The shipped model folders that are present (v4, A-aera-7: the skill folder copied alone has no plugin
    around it): SKILL/examples/*, SKILL/templates/*, and PLUGIN/templates/* only when PLUGIN is the plugin (its
    .claude-plugin/plugin.json exists)."""
    parents = [SKILL / "examples", SKILL / "templates"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        parents.append(PLUGIN / "templates")
    return [d for parent in parents if parent.is_dir() for d in sorted(parent.iterdir())
            if (d / "model.spec.json").is_file()]


SHIPPED = shipped_models()


def rules(rep) -> list[str]:
    return [f.rule for f in rep.findings]


def lines(rep) -> list[str]:
    return [f.line() for f in rep.findings] + list(rep.notes)


def _pq_spec() -> dict:
    spec = base_spec()
    _sheet(spec, "raw_sales")["landing"] = "power_query"
    return spec


def _files(*paths: Path) -> dict[str, bytes | None]:
    return {p.as_posix(): (p.read_bytes() if p.is_file() else None) for p in paths}


def _total(n: int) -> float:
    return sum(100 + 7.5 * i for i in range(n))


# ============================================================================= A. [A-aera-1, A-aera-4, C-sol-1]

@pytest.mark.excel
def test_accept_refresh_after_land_force_keeps_the_csv(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m", spec=_pq_spec(), contract=_rows_only_contract())
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data["power_query_attached"] == ["sales"], lines(rep)
    rows = sales_rows()
    rows[0]["amount"] = str(float(rows[0]["amount"]) + 1)          # one changed row, landed after the build
    assert land.land_csv(root, "sales", _write_rows(tmp_path / "changed.csv", rows), force=True).exit_code == 0
    snap = root / "raw" / "sales.csv"
    before = _files(snap, manifest.manifest_path(root / "model.xlsx"))
    landed = manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")["digest"]
    rep = check.check_model(root, accept_refresh=True, strict=True)
    assert rep.exit_code == 1, lines(rep)
    newer = [f for f in rep.findings if f.rule == "R-CHANGED" and "rows landed after that" in f.message]
    assert newer and "nothing was accepted and raw/sales.csv was not changed" in newer[0].message
    assert "--refresh" in newer[0].fix and "build" in newer[0].fix
    assert "R-ACCEPTED" not in rules(rep)
    assert snap.read_bytes() == before[snap.as_posix()]
    assert manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")["digest"] == landed


def test_accept_refresh_without_excel_accepts_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path / "a", 10, capacity=12, landing="power_query")
    snap, ctl_file = m.root / "raw" / "sales.csv", controls.path(m.root, "sales")
    watched = (snap, ctl_file, manifest.manifest_path(m.wb))
    # (a) rows a refresh saved earlier (openpyxl stands in): --refresh asked for, and it cannot run
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    before = _files(*watched)
    rep = check.check_model(m.root, refresh=True, accept_refresh=True, controls={"sales.amount": _total(12)},
                            control_texts=[f"sales.amount={_total(12)}"])
    assert rep.exit_code != 0
    assert any(n.startswith("no table was accepted: --refresh was asked for and no refresh ran") and
               "the manifest were not changed" in n for n in rep.notes), rep.notes
    assert "R-ACCEPTED" not in rules(rep) and "R-CHANGED" in rules(rep)
    assert _files(*watched) == before and before[ctl_file.as_posix()] is None
    # (b) land --force with one changed row, then the same run
    m = tc.make_model(tmp_path / "b", 10, landing="power_query")
    snap, ctl_file = m.root / "raw" / "sales.csv", controls.path(m.root, "sales")
    watched = (snap, ctl_file, manifest.manifest_path(m.wb))
    rows = tc.sales_values(10)
    rows[3][4] = 999.0
    assert land.land_csv(m.root, "sales", tc.write_source_csv(tmp_path / "b_changed.csv", 10, rows=rows),
                         force=True).exit_code == 0
    before = _files(*watched)
    rep = check.check_model(m.root, refresh=True, accept_refresh=True)
    assert rep.exit_code != 0 and "R-ACCEPTED" not in rules(rep)
    assert any(n.startswith("no table was accepted") for n in rep.notes)
    assert _files(*watched) == before


def test_inputs_sheet_value_is_not_a_control_without_excel(tmp_path: Path, monkeypatch) -> None:
    """The same rule as the Excel test below, with the Excel job replaced (fake_excel): the value typed into the
    inputs sheet's control cell is not taken as the control total and is not recorded."""
    import openpyxl
    m = tc.make_model(tmp_path, 10, capacity=12, total=_total(10), landing="power_query")
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    book = openpyxl.load_workbook(m.wb)
    book["inputs"]["D9"] = _total(12)                   # the new total typed into the control cell
    book.save(m.wb)
    man = manifest.load_manifest(m.wb)
    row = common.split_cell(common.parse_ref(next(c["result"] for c in man["checks"]
                                                  if c["id"] == "sales_total_amount"))[1])[1]
    fake_excel(monkeypatch, {f"checks!D{row}:H{row}": [[_total(12), _total(12), None, 0, 0]]})
    rep = check.check_model(m.root, accept_refresh=True, strict=True)
    assert rep.exit_code == 1
    assert "D-TOTAL" in rules(rep) and "R-ACCEPTED" not in rules(rep), lines(rep)
    w = next(f for f in rep.findings if f.rule == "D-CONTROL-INPUT")
    assert w.severity == "warning" and "inputs!D9 holds 1,695" in w.message and "1,337.5 (the contract)" in w.message
    assert "--accept-refresh --control sales.amount=<source total>" in w.fix
    assert not controls.path(m.root, "sales").exists()
    total = next(f for f in rep.findings if f.rule == "D-TOTAL")
    assert "the control total from the contract is 1,337.5" in total.message


@pytest.mark.excel
def test_inputs_sheet_value_is_not_a_control(tmp_path: Path) -> None:
    from lib import excel
    root = make_model(tmp_path / "m", spec=_pq_spec())
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data["power_query_attached"] == ["sales"], lines(rep)
    rows = sales_rows()
    more = rows + [dict(r, invoice_id=f"9{r['invoice_id'][1:]}") for r in rows[:10]]
    total = sum(float(r["amount"]) for r in more)
    _write_rows(root / "raw" / "sales.csv", more)              # the source changed; the query reads this file
    assert check.check_model(root, refresh=True).data["refreshed"] is True
    assert check.extend_helpers(root, table="sales").exit_code == 0
    wb = root / "model.xlsx"
    sheet, cell = common.parse_ref(manifest.load_manifest(wb)["inputs"]["sales_total_amount"])
    res = excel.apply_edits(wb, [{"op": "value", "sheet": sheet, "cell": cell, "value": total}], out=wb)
    assert res.ok, res.message()
    rep = check.check_model(root, accept_refresh=True, strict=True)
    assert rep.exit_code == 1, lines(rep)
    assert "D-TOTAL" in rules(rep) and "D-CONTROL-INPUT" in rules(rep) and "R-ACCEPTED" not in rules(rep)
    path = controls.path(root, "sales")
    assert not path.exists() or all(float(t["value"]) != total for e in common.read_json(path)["accepted"]
                                    for t in e["totals"])


def test_contract_agreed_again_fails_check_and_audit_until_rebuild(tmp_path: Path, monkeypatch) -> None:
    """Integration test I-1: the audit part needs P8's CT-VERSION in audit.py."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.real_model(tmp_path, 10)
    man = manifest.load_manifest(m.wb)
    agreed = common.read_json(modelfolder.contract_path(m.root, "sales"))["agreement"]["contract_sha256"]
    assert manifest.raw_sheet(man, "sales")["contract_sha256"] == agreed
    contract.propose(m.root, "sales", csv=m.source, key="invoice_id,line_no", grain="one row per invoice line",
                     rows="1:100000", units=["amount=KRW", "line_no=count"], totals=[f"amount={_total(12)}"],
                     force=True)
    contract.show(m.root, "sales")
    task = tmp_path / "task2.md"
    task.write_text(f"Contract reply: {tc.REPLY}\n", encoding="utf-8")
    contract.agree(m.root, "sales", reply=tc.REPLY, by="Max", channel="task-file", source_file=task)
    rep = check.check_model(m.root, strict=True)
    assert rep.exit_code == 1 and "D-TOTAL" in rules(rep), lines(rep)
    ver = next(f for f in rep.findings if f.rule == "CT-VERSION")
    assert ver.severity == "error" and "raw_sales was built from another version of its contract" in ver.message
    assert "land" in ver.fix and "--force" in ver.fix and "build" in ver.fix
    rep = audit.audit_workbook(m.wb, strict=True)
    assert rep.exit_code == 1 and "CT-VERSION" in rules(rep), lines(rep)           # I-1 (P8)
    assert land.land_csv(m.root, "sales", tc.write_source_csv(tmp_path / "src12.csv", 12), force=True).exit_code == 0
    rep = build.build(m.root / "model.spec.json", force=True, power_query=False)
    assert rep.exit_code == 0, lines(rep)
    rep = check.check_model(m.root, strict=True)
    assert rep.exit_code == 3 and rep.counts()["errors"] == 0 and rep.counts()["warnings"] == 0, lines(rep)
    rep = audit.audit_workbook(m.wb, strict=True)
    assert "CT-VERSION" not in rules(rep) and rep.exit_code == 0, lines(rep)


def test_a_manifest_without_a_contract_hash_gets_a_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10)
    man = manifest.load_manifest(m.wb)
    manifest.raw_sheet(man, "sales").pop("contract_sha256", None)
    manifest.write_manifest(m.wb, man)
    rep = check.check_model(m.root)
    assert "CT-VERSION" not in rules(rep)
    assert ("raw_sales: the manifest records no contract hash (built before v3), so the contract version was not "
            "compared") in rep.notes


def test_a_manifest_without_a_workbook_digest_accepts_no_changed_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, capacity=12, landing="power_query", query_table=True)
    man = manifest.load_manifest(m.wb)
    manifest.raw_sheet(man, "sales").pop("workbook_digest", None)
    manifest.write_manifest(m.wb, man)
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    before = _files(m.root / "raw" / "sales.csv")
    rep = check.check_model(m.root, accept_refresh=True)
    f = next(x for x in rep.findings if x.rule == "R-CHANGED")
    assert "records no workbook digest for raw_sales (built before v3)" in f.message and "build" in f.fix
    assert "R-ACCEPTED" not in rules(rep) and _files(m.root / "raw" / "sales.csv") == before


def test_every_check_records_the_workbook_digest_when_the_rows_equal_the_landing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, capacity=12, landing="power_query", query_table=True)
    man = manifest.load_manifest(m.wb)
    entry = manifest.raw_sheet(man, "sales")
    entry["workbook_digest"] = "sha256:" + "0" * 64
    manifest.write_manifest(m.wb, man)
    check.check_model(m.root)
    entry = manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")
    assert entry["workbook_digest"] == entry["digest"]
    # an accepted refresh records the accepted digest as the workbook's
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    rep = check.check_model(m.root, accept_refresh=True)
    assert "R-ACCEPTED" in rules(rep), lines(rep)
    entry = manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")
    assert entry["workbook_digest"] == entry["digest"] == manifest.raw_digest(tc.HEADER, tc.sales_values(12), tc.TYPES)


def test_total_check_fix_says_find_the_rows_and_accept_with_a_control(tmp_path: Path) -> None:
    m = tc.make_model(tmp_path, 10, total=_total(10))
    fix = check._fix_for_check("sales_total_amount", m.root, manifest.load_manifest(m.wb))
    assert "find the missing or extra rows in raw_sales" in fix
    assert "--accept-refresh --control sales.amount=<source total>" in fix
    assert "type" not in fix


def test_build_records_the_contract_hash_and_the_workbook_digest(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    entry = manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")
    agreed = common.read_json(root / "contracts" / "sales.contract.json")["agreement"]["contract_sha256"]
    assert entry["contract_sha256"] == agreed
    assert entry["workbook_digest"] == entry["digest"]


# ============================================================================= B. [A-aera-2]

def _touch(wb: Path, tag: str) -> str:
    """Change the workbook file (stands in for a save by Excel); returns its new sha256."""
    import openpyxl
    book = openpyxl.load_workbook(wb)
    book["notes"]["B50"] = tag
    book.save(wb)
    return common.file_sha256(wb)


def test_edit_then_check_is_not_owned_without_excel(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    edited = _touch(wb, "typed in Excel")                       # an edit no rotm.py command recorded
    out = _touch(wb, "saved by check")
    modelfolder.record_step(root, "model.xlsx", "check", {"input_sha256": edited, "output_sha256": out,
                                                          "recalculated": True})
    assert out not in build._known_hashes(root, wb)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json", strict=True)
    assert "changed after rotm.py last wrote it" in ei.value.message


def test_long_cycle_stays_owned(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    for k, step in enumerate(("check", "extend", "check", "check")):
        inp = common.file_sha256(wb)
        out = _touch(wb, f"{step} {k}")
        rec = {"input_sha256": inp, "output_sha256": out}
        if step == "check":
            rec["recalculated"] = True
        modelfolder.record_step(root, "model.xlsx", step, rec)
    writes = common.read_json(root / "reports" / "writes.json")
    pairs = writes["workbooks"]["model.xlsx"]
    assert [p["step"] for p in pairs] == ["build", "check", "extend", "check", "check"]
    assert common.file_sha256(wb) in build._known_hashes(root, wb)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, lines(rep)
    assert not list(root.glob("model.bak_*.xlsx"))
    # status.json keeps its shape (one record per step); the history is in writes.json only
    st = modelfolder.read_status(root)["workbooks"]["model.xlsx"]
    assert {"build", "check", "extend"} <= set(st) <= set(modelfolder.STEPS)
    assert common.read_json(modelfolder.status_path(root))["rotm_status"] == 1


def test_a_record_without_an_input_hash_does_not_count(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    out = _touch(wb, "theme")
    modelfolder.record_step(root, "model.xlsx", "theme", {"output_sha256": out})
    assert out not in build._known_hashes(root, wb)


def test_status_prints_the_same_after_writes_json(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    r = tc.run_cli("status", str(root), env_extra={"ROTM_NO_EXCEL": "1"})
    assert r.returncode == 0 and "build" in r.stdout and "writes" not in r.stdout, r.stdout + r.stderr


@pytest.mark.excel
def test_edit_in_excel_then_check_then_build_is_refused(tmp_path: Path) -> None:
    import openpyxl
    from lib import excel
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    man = manifest.load_manifest(wb)
    s_sheet, s_cell = common.parse_ref(man["inputs"]["scale"])
    g_sheet, g_rng = common.parse_ref(man["inputs"]["target"])
    c1, r1, _c2, _r2 = common.range_bounds(g_rng)
    edits = [(s_sheet, s_cell, 500), (g_sheet, f"{common.col_letter(c1)}{r1}", 4321),
             (g_sheet, f"{common.col_letter(c1 + 1)}{r1}", 8765)]
    res = excel.apply_edits(wb, [{"op": "value", "sheet": s, "cell": c, "value": v} for s, c, v in edits], out=wb)
    assert res.ok, res.message()
    rep = check.check_model(root, strict=True)
    assert rep.verified["recalculated"] is True, lines(rep)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json", strict=True)
    assert "changed after rotm.py last wrote it" in ei.value.message
    r = tc.run_cli("build", str(root / "model.spec.json"), "--strict")
    assert r.returncode == 2, r.stdout + r.stderr
    book = openpyxl.load_workbook(wb)
    assert [book[s][c].value for s, c, _v in edits] == [v for _s, _c, v in edits]
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert rep.exit_code == 0, lines(rep)
    backups = list(root.glob("model.bak_*.xlsx"))
    assert len(backups) == 1
    kept = openpyxl.load_workbook(backups[0])
    assert [kept[s][c].value for s, c, _v in edits] == [v for _s, _c, v in edits]


@pytest.mark.excel
def test_check_of_an_unedited_workbook_then_build_exits_0(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    built = common.file_sha256(wb)
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0 and common.file_sha256(wb) != built, lines(rep)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, lines(rep)
    assert not list(root.glob("model.bak_*.xlsx"))


# ============================================================================= C. [J-1]

def test_checks_columns_fit_a_long_control_total(tmp_path: Path) -> None:
    c = base_contract()
    c["expect"]["totals"][0]["value"] = 264697351
    for col in c["columns"]:
        if col["name"] == "amount":
            col["format"] = "amount2"
    rows = sales_rows()
    rows[-1]["amount"] = str(int(float(rows[-1]["amount"])) + 264697351 - 31435000)
    root = make_model(tmp_path / "m", contract=c, csv_path=_write_rows(tmp_path / "big.csv", rows))
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, lines(rep)
    wb = load(root / "model.xlsx")
    text = "264,697,351.00"                                     # 14 characters in amount2
    ws = wb["checks"]
    for col in "DEFGH":
        assert ws.column_dimensions[col].width >= len(text) + 1, (col, ws.column_dimensions[col].width)
    r = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value == "sales_total_amount")
    assert ws.cell(r, 5).number_format == ws.cell(r, 4).number_format
    sheet, cell = common.parse_ref(manifest.load_manifest(root / "model.xlsx")["inputs"]["sales_total_amount"])
    col = re.match(r"[A-Z]+", cell).group(0)
    assert wb[sheet].column_dimensions[col].width >= len(text) + 1


# ============================================================================= D. [A-aera-5, C-sol-2]

STATE_WORDS = {"missing": "missing", "invalid": "invalid", "proposed": "proposed", "stale": "stale",
               "example": "agreed only on the example channel"}


@pytest.mark.parametrize("state", list(STATE_WORDS))
def test_refresh_refused_for_each_contract_state(outside_tmp: Path, monkeypatch, state: str) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    # v5: outside the plugin wherever --basetemp points (conftest.py), so the 'example' channel is refused here
    m = tc.make_model(outside_tmp, 10, agree=state != "proposed", landing="power_query")
    path = modelfolder.contract_path(m.root, "sales")
    if state == "missing":
        path.unlink()
    elif state == "invalid":
        path.write_text('{"rotm_contract": 1}', encoding="utf-8")
    elif state == "stale":
        data = common.read_json(path)
        data["purpose"] = "changed after the agreement"
        common.write_json(path, data)
    elif state == "example":
        data = common.read_json(path)
        data["agreement"]["channel"] = "example"
        common.write_json(path, data)
    before = common.file_sha256(m.wb)
    with pytest.raises(common.Refusal) as ei:
        check.check_model(m.root, refresh=True)
    text = refusal_lines(ei.value)
    assert ei.value.message.startswith("--refresh refused: 1 raw table(s) loaded by a query have no agreed contract")
    assert "nothing was refreshed" in ei.value.message
    assert f"raw_sales: the contract is {STATE_WORDS[state]}" in text
    assert "contract" in (ei.value.fix or "") and "--table sales" in text
    assert common.file_sha256(m.wb) == before
    if state == "proposed":
        r = tc.run_cli("check", str(m.root), "--refresh", env_extra={"ROTM_NO_EXCEL": "1"})
        assert r.returncode == 2 and "nothing was refreshed" in r.stderr + r.stdout
        assert common.file_sha256(m.wb) == before


@pytest.mark.excel
def test_refresh_runs_with_every_contract_agreed(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m", spec=_pq_spec())
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data["power_query_attached"] == ["sales"], lines(rep)
    rep = check.check_model(root, refresh=True, strict=True)
    assert rep.exit_code == 0 and rep.data["refreshed"] is True, lines(rep)


# ============================================================================= E. [C-sol-7]

_QT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/queryTable"


def _make_query_table(wb: Path, display_name: str, query: str) -> None:
    """Turn the table `display_name` of a saved workbook into a query table of connection 1 (the parts Excel writes
    for a query loaded to a table), so the package reader can be tested without Excel."""
    with zipfile.ZipFile(wb) as z:
        items = {n: z.read(n) for n in z.namelist()}
    table = next(n for n, b in items.items() if re.match(r"xl/tables/table\d+\.xml$", n)
                 and f'displayName="{display_name}"'.encode() in b)
    items[table] = re.sub(rb"<table\b", b'<table tableType="queryTable"', items[table], count=1)
    folder, name = table.rsplit("/", 1)
    items[f"{folder}/_rels/{name}.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.'
        f'org/package/2006/relationships"><Relationship Id="rId1" Type="{_QT_REL}" '
        'Target="../queryTables/queryTable1.xml"/></Relationships>').encode()
    items["xl/queryTables/queryTable1.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><queryTable xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" name="ExternalData_1" connectionId="1" autoFormatId="16"/>').encode()
    items["xl/connections.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><connections xmlns="http://schemas.openxmlformats.org/'
        f'spreadsheetml/2006/main"><connection id="1" keepAlive="1" name="Query - {query}" type="5" '
        'refreshedVersion="8" saveData="1"><dbPr connection="Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;'
        f'Location={query};Extended Properties=&quot;&quot;" command="SELECT * FROM [{query}]"/></connection>'
        '</connections>').encode()
    tmp = wb.with_name(wb.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in items.items():
            z.writestr(n, b)
    tmp.replace(wb)


def test_a_query_table_at_a1_counts_as_loaded_by_a_query(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, capacity=12)               # the manifest says static
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    _make_query_table(m.wb, "tbl_raw_sales", "q_by_hand")
    assert check.query_tables(m.wb) == {"raw_sales": "q_by_hand"}
    rep = check.check_model(m.root, accept_refresh=True)
    assert "R-ACCEPTED" in rules(rep), lines(rep)
    entry = manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")
    assert entry["landing"] == "power_query" and entry["query_attach"] == "hand"
    assert entry["query_name"] == "q_by_hand" and "query_contract_sha256" not in entry
    rep = check.check_model(m.root, refresh=True)
    assert any(n.startswith("--refresh: raw_sales's query q_by_hand was attached by hand") for n in rep.notes)


@pytest.mark.excel
def test_hand_attached_query_is_accepted(tmp_path: Path) -> None:
    from lib import excel
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    rows = sales_rows()
    more = rows + [dict(rows[0], invoice_id="900001")]
    total = sum(float(r["amount"]) for r in more)
    src = _write_rows(tmp_path / "source" / "sales.csv", more)
    c = common.read_json(root / "contracts" / "sales.contract.json")
    res = excel.pq_attach(wb, sheet="raw_sales", table_name="tbl_raw_sales", query_name="q_sales",
                          m_text=land.m_text(c, csv_path=src))
    assert res.ok, res.message()
    assert check.query_tables(wb) == {"raw_sales": "q_sales"}
    assert check.extend_helpers(root, table="sales").exit_code == 0
    rep = check.check_model(root, accept_refresh=True, controls={"sales.amount": total},
                            control_texts=[f"sales.amount={total:.0f}"], strict=True)
    assert rep.exit_code == 0 and "R-ACCEPTED" in rules(rep), lines(rep)
    entry = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    assert entry["query_attach"] == "hand" and entry["query_name"] == "q_sales" and entry["landing"] == "power_query"
    assert "query_contract_sha256" not in entry
    rep = check.check_model(root, refresh=True, strict=True)
    assert any("q_sales was attached by hand" in n for n in rep.notes), lines(rep)
    assert rep.exit_code == 0, lines(rep)


def test_static_table_edited_by_hand_is_still_refused(tmp_path: Path, monkeypatch) -> None:
    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    book = openpyxl.load_workbook(wb)
    book["raw_sales"]["F2"] = 12345
    book.save(wb)
    assert check.query_tables(wb) == {}
    rep = check.check_model(root, accept_refresh=True)
    f = next(x for x in rep.findings if x.rule == "R-CHANGED")
    assert "static table" in f.message and "R-ACCEPTED" not in rules(rep)


# ============================================================================= F. [A-aera-8, C-sol-6]

def _empty_contract() -> dict:
    c = _rows_only_contract()
    c["expect"] = {"rows": {"min": 0, "max": 1000}}
    c["refresh"]["allow_empty"] = True
    return c


def _header_only(path: Path, header: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(header) + "\n", encoding="utf-8")
    return path


def _raw_rows(wb: Path, rows: list[list]) -> None:
    """Replace the rows of raw_sales with openpyxl (stands in for a refresh in tests without Excel)."""
    import openpyxl
    book = openpyxl.load_workbook(wb)
    ws = book["raw_sales"]
    ws.delete_rows(2, ws.max_row)
    for r in rows:
        ws.append(r)
    ws.tables["tbl_raw_sales"].ref = f"A1:F{max(2, len(rows) + 1)}"
    book.save(wb)


def test_zero_rows_build_with_zero_helper_rows_and_extend_both_ways(tmp_path: Path, monkeypatch) -> None:
    import datetime as dt

    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    header = list(sales_rows()[0])
    root = make_model(tmp_path / "m", contract=_empty_contract(), csv_path=_header_only(tmp_path / "e.csv", header))
    wb = root / "model.xlsx"
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, lines(rep)
    man = manifest.load_manifest(wb)
    h = manifest.helper_sheet(man, "sales")
    assert (h["capacity"], h["first_row"], h["last_row"]) == (0, 2, 1)
    assert h["columns"][0]["formula"] == "=ROW()" and all(c["formula"] != "=" for c in h["columns"])
    ws = openpyxl.load_workbook(wb)["h_sales"]
    assert all(ws.cell(2, c).value is None for c in range(1, len(h["columns"]) + 1))
    assert check.coverage(wb, man) == []
    rep = check.check_model(root, strict=True)
    assert rep.counts()["errors"] == 0 and rep.counts()["warnings"] == 0, lines(rep)
    # a later refresh returns 3 rows: extend writes row 2 from the manifest's formulas and fills down
    man["sheets"][[s["name"] for s in man["sheets"]].index("raw_sales")]["landing"] = "power_query"
    manifest.write_manifest(wb, man)
    three = [["000901", 1, dt.datetime(2026, 1, 5), "East", "P1", 10.0],
             ["000902", 1, dt.datetime(2026, 2, 5), "West", "P2", 20.0],
             ["000903", 1, dt.datetime(2026, 3, 5), "South", "P3", 30.0]]
    _raw_rows(wb, three)
    rep = check.extend_helpers(root, table="sales")
    assert rep.exit_code == 0, lines(rep)
    assert "H-EXTENDED" in rules(rep)
    ws = openpyxl.load_workbook(wb)["h_sales"]
    assert ws["A2"].value == "=ROW()" and ws["A4"].value == "=ROW()" and ws["A5"].value is None
    assert ws["B4"].value == '=--(INDEX(raw_sales!$A:$A,A4)<>"")'
    h = manifest.helper_sheet(manifest.load_manifest(wb), "sales")
    assert (h["capacity"], h["last_row"]) == (3, 4)
    assert check.coverage(wb, manifest.load_manifest(wb)) == []
    # and back to no rows: extend clears every helper row, row 2 included
    _raw_rows(wb, [])
    rep = check.extend_helpers(root, table="sales")
    assert rep.exit_code == 0 and "H-CLEARED" in rules(rep), lines(rep)
    ws = openpyxl.load_workbook(wb)["h_sales"]
    assert all(ws.cell(r, c).value is None for r in (2, 3, 4) for c in range(1, len(h["columns"]) + 1))
    h = manifest.helper_sheet(manifest.load_manifest(wb), "sales")
    assert (h["capacity"], h["last_row"]) == (0, 1)
    assert check.coverage(wb, manifest.load_manifest(wb)) == []


@pytest.mark.excel
def test_header_only_csv_builds_audits_and_checks(tmp_path: Path) -> None:
    """Integration test I-3: audit (P8 part G) and lift (P5 part C) with zero rows."""
    from lib import lift
    root = tmp_path / "e"
    modelfolder.init_model(root, name="empty_test")
    sample = _header_only(tmp_path / "src" / "sales.csv", tc.HEADER)
    contract.propose(root, "sales", csv=sample, key="invoice_id,line_no", grain="one row per invoice line",
                     rows="0:10", units=["amount=KRW", "line_no=count"], totals=["amount=0"], allow_empty=True,
                     types=["line_no=integer", "inv_date=date", "amount=number"])
    task = tmp_path / "task.md"
    task.write_text(f"Contract reply: {tc.REPLY}\n", encoding="utf-8")
    contract.agree(root, "sales", reply=tc.REPLY, by="Max", channel="task-file", source_file=task)
    assert land.land_csv(root, "sales", sample).exit_code == 0
    common.write_json(root / "model.spec.json", tc.REAL_SPEC)
    rep = build.build(root / "model.spec.json", strict=True, power_query=False)
    assert rep.exit_code == 0, lines(rep)
    h = manifest.helper_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")
    assert (h["capacity"], h["last_row"]) == (0, 1)
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0, lines(rep)
    rep = audit.audit_workbook(root / "model.xlsx", strict=True)
    assert rep.exit_code == 0, lines(rep)                                           # I-3 (P8 part G)
    rep = lift.lift_model(root, run=True)
    assert rep.exit_code == 0 and rep.data["tie_out"]["status"] == "tie", lines(rep)   # I-3 (P5 part C)


@pytest.mark.excel
def test_empty_table_through_power_query_one_zero_one(tmp_path: Path) -> None:
    root = tmp_path / "pq"
    modelfolder.init_model(root, name="empty_pq")
    sample = tc.write_source_csv(tmp_path / "src" / "sales.csv", 1)
    contract.propose(root, "sales", csv=sample, key="invoice_id,line_no", grain="one row per invoice line",
                     rows="0:10", units=["amount=KRW", "line_no=count"], totals=[f"amount={_total(1)}"],
                     allow_empty=True)
    task = tmp_path / "task.md"
    task.write_text(f"Contract reply: {tc.REPLY}\n", encoding="utf-8")
    contract.agree(root, "sales", reply=tc.REPLY, by="Max", channel="task-file", source_file=task)
    assert land.land_csv(root, "sales", sample).exit_code == 0
    spec = dict(tc.REAL_SPEC, sheets=[dict(s) for s in tc.REAL_SPEC["sheets"]])
    spec["sheets"][0]["landing"] = "power_query"
    common.write_json(root / "model.spec.json", spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data["power_query_attached"] == ["sales"], lines(rep)
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0, lines(rep)
    for n in (0, 1):
        tc.write_source_csv(root / "raw" / "sales.csv", n)        # the source changed; the query reads this file
        rep = check.check_model(root, refresh=True)
        assert rep.data["refreshed"] is True, lines(rep)
        rep = check.extend_helpers(root, table="sales")
        assert rep.exit_code == 0, lines(rep)
        h = manifest.helper_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")
        assert (h["capacity"], h["last_row"]) == (n, n + 1)
        if n:
            import openpyxl
            ws = openpyxl.load_workbook(root / "model.xlsx")["h_sales"]
            assert all(ws[f"{c['col']}2"].data_type == "f" for c in h["columns"]), \
                [(c["col"], ws[f"{c['col']}2"].value) for c in h["columns"]]      # formulas, not text
        rep = check.check_model(root, accept_refresh=True, controls={"sales.amount": _total(n)},
                                control_texts=[f"sales.amount={_total(n)}"])
        assert rep.exit_code == 0 and "R-ACCEPTED" in rules(rep), lines(rep)
        rep = check.check_model(root, strict=True)
        assert rep.exit_code == 0, (n, lines(rep))


# ============================================================================= G. [A-aera-10]

def _number_as_text(tmp_path: Path) -> Path:
    import openpyxl
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    book = openpyxl.load_workbook(wb)
    ws = book["raw_sales"]
    assert ws["B2"].value == 1
    ws["B2"].value = "1"                                         # the number 1 becomes the text '1'
    book.save(wb)
    assert openpyxl.load_workbook(wb)["raw_sales"]["B2"].data_type == "s"
    return root


def test_number_stored_as_text_fails_check(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _number_as_text(tmp_path)
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 1
    f = next(x for x in rep.findings if x.rule == "D-TYPE")
    assert f.severity == "error" and (f.sheet, f.cell) == ("raw_sales", "B2")
    assert "1 cell(s) of integer column line_no in raw_sales are stored as text (first B2: '1')" == f.message
    assert "R-CHANGED" in rules(rep)


def test_number_stored_as_text_fails_audit_too(tmp_path: Path) -> None:
    """Integration test I-2: needs P8's R-TYPE in audit.py."""
    root = _number_as_text(tmp_path)
    rep = audit.audit_workbook(root / "model.xlsx", strict=True)
    assert rep.exit_code == 1
    assert any(f.rule == "R-TYPE" and f.cell == "B2" for f in rep.findings), lines(rep)


# ============================================================================= H. [A-aera-19]

def _copy_model(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.xlsx", "*.manifest.json", "reports", "lift",
                                                             "__pycache__"))
    return dest


def test_self_check_warns(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(specmod.EXAMPLE_ROOTS_ENV, str(tmp_path))
    root = _copy_model(SKILL / "templates" / "starter", tmp_path / "starter")
    spec = common.read_json(root / "model.spec.json")
    _sheet(spec, "checks")["checks"].append({"id": "self_tie", "kind": "equal", "label": "Grid total equals itself",
                                              "actual": "{blk:spend.total}", "expected": "{blk:spend.total}"})
    common.write_json(root / "model.spec.json", spec)
    rep = build.build(root / "model.spec.json", strict=True, power_query=False)
    assert rep.exit_code == 1
    w = [f for f in rep.findings if f.rule == "W-SELF-CHECK"]
    assert len(w) == 1 and w[0].severity == "warning", lines(rep)
    assert w[0].message.startswith("check self_tie: actual (") and "are the same, so the check can never fail" in \
        w[0].message
    assert "point one side at an independent total" in w[0].fix


def test_shipped_models_have_no_self_check(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(specmod.EXAMPLE_ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    assert SHIPPED, "no shipped model folder found"
    for folder in SHIPPED:
        root = _copy_model(folder, tmp_path / folder.name)
        rep = build.build(root / "model.spec.json", power_query=False)
        assert "W-SELF-CHECK" not in rules(rep), (folder.name, lines(rep))
        assert rep.counts()["errors"] == 0, (folder.name, lines(rep))


# ============================================================================= I. [C-sol-5]

def _two_part_key_model(tmp_path: Path) -> Path:
    from test_build import agree
    root = tmp_path / "k"
    modelfolder.init_model(root, name="keys_sep")
    c = {"rotm_contract": 1, "table": "k", "raw_sheet": "raw_k", "excel_table": "tbl_raw_k",
         "purpose": "key separator test", "source": dict(base_contract()["source"]),
         "grain": "one row per id and part", "key": ["id", "part"],
         "columns": [{"name": "id", "type": "text", "unit": None, "null_rule": "never", "allowed": None,
                      "source_expression": "id"},
                     {"name": "part", "type": "text", "unit": None, "null_rule": "never", "allowed": None,
                      "source_expression": "part"},
                     {"name": "amount", "type": "number", "unit": "KRW", "null_rule": "never", "allowed": None,
                      "source_expression": "amount"}],
         "expect": {"rows": {"min": 1, "max": 1000}}, "refresh": {"frequency": None, "capacity_rows": None,
                                                                    "allow_empty": False}}
    common.write_json(root / "contracts" / "k.contract.json", agree(c))
    src = tmp_path / "k.csv"
    with open(src, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["id", "part", "amount"])
        w.writerows([["A", "B", 1], ["A", "C", 2]])
    assert land.land_csv(root, "k", src).exit_code == 0
    common.write_json(root / "model.spec.json", {"rotm_spec": 1, "name": "keys_sep", "title": "Key separator", "sheets": [
        {"name": "raw_k", "type": "raw", "table": "k"},
        {"name": "h_k", "type": "helpers", "table": "k"},
        {"name": "calc", "type": "calc", "blocks": [
            {"id": "tot", "title": "Total", "unit": "KRW", "rows": {"values": ["all"]}, "cell": "SUM({h:k.amount})",
             "key": True}]},
        {"name": "checks", "type": "checks", "checks": [
            {"id": "tot_ties", "kind": "equal", "label": "Total ties to raw", "actual": "{cell:tot}",
             "expected": "SUM({raw:k.amount})"}]}]})
    return root


def test_key_separator_warns_and_no_d_key(tmp_path: Path, monkeypatch) -> None:
    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _two_part_key_model(tmp_path)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    h = manifest.helper_sheet(manifest.load_manifest(root / "model.xlsx"), "k")
    assert any(c["kind"] == "dupkey" for c in h["columns"])
    book = openpyxl.load_workbook(root / "model.xlsx")
    ws = book["raw_k"]
    ws["A2"], ws["B2"] = "A|B", "C"
    ws["A3"], ws["B3"] = "A", "B|C"
    book.save(root / "model.xlsx")
    rep = check.check_model(root)
    w = next(f for f in rep.findings if f.rule == "D-KEY-SEPARATOR")
    assert w.severity == "warning" and w.message.startswith("raw_k row 2: key column id holds '|'")
    assert "h_k column dup_key" in w.message and "the key check of rotm.py check stands" in w.message
    assert "build" in w.fix and "--force" in w.fix
    assert "D-KEY" not in rules(rep)


# ============================================================================= J. [J-2]

def test_date_list_row_headers_show_dates(tmp_path: Path) -> None:
    """Integration test I-4: the strict audit needs P8's S-NUMFMT rule HDR-FORMAT."""
    import openpyxl
    spec = base_spec()
    months = ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30"]
    _sheet(spec, "inputs")["items"].append({"id": "months", "kind": "list", "label": "Months", "values": months,
                                            "type": "date", "source": "test"})
    _sheet(spec, "calc")["blocks"].append({"id": "by_month", "title": "Amount by month", "unit": "KRW",
                                           "format": "amount", "rows": {"in": "months", "label": "Month"},
                                           "cell": "SUMIFS({h:sales.amount},{h:sales.month_end},{row})",
                                           "total": {"row": True}})
    _sheet(spec, "checks")["checks"].append({"id": "by_month_ties", "kind": "equal",
                                              "label": "Monthly total equals the raw total",
                                              "actual": "{blk:by_month.total}", "expected": "SUM({raw:sales.amount})"})
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, lines(rep)
    block = next(b for b in manifest.sheet_entry(manifest.load_manifest(root / "model.xlsx"), "calc")["blocks"]
                 if b["id"] == "by_month")
    c1, r1, _c2, r2 = common.range_bounds(block["labels"])
    ws = openpyxl.load_workbook(root / "model.xlsx")["calc"]
    date_fmt = specmod.load_spec(root / "model.spec.json").dictionary.number_format("date")
    assert [ws.cell(r, c1).number_format for r in range(r1, r2 + 1)] == [date_fmt] * len(months)
    assert ws.cell(r2 + 1, c1).number_format == "General"                   # the Total label stays General
    rep = audit.audit_workbook(root / "model.xlsx", strict=True)
    assert rep.exit_code == 0, lines(rep)                                     # I-4 (P8)


# ============================================================================= K. supporting [A-aera-12, C-sol-9, C-sol-10] (5)

def test_refresh_without_a_query_warns(tmp_path: Path, monkeypatch) -> None:
    m = tc.make_model(tmp_path, 10)
    fake_excel(monkeypatch, {})
    rep = check.check_model(m.root, refresh=True, strict=True)
    x = next(f for f in rep.findings if f.rule == "X-NO-QUERY")
    assert x.severity == "warning" and rep.exit_code == 1
    assert x.message == ("--refresh was asked for, but the workbook has no Power Query query or connection: the raw "
                         "sheets are static snapshots and nothing was refreshed")
    assert "land" in x.fix and "--power-query" in x.fix
    assert check.check_model(m.root, refresh=True).exit_code == 0
    rep = check.check_model(m.root, refresh=True, accept_refresh=True)
    assert rep.exit_code == 1 and next(f for f in rep.findings if f.rule == "X-NO-QUERY").severity == "error"
    assert any(n.startswith("no table was accepted") for n in rep.notes)


# ============================================================================= L. supporting [A-aera-15, A-aera-17]

def test_no_value_fix_names_a_cycle_only_for_circular_cells(tmp_path: Path, monkeypatch) -> None:
    m = tc.make_model(tmp_path, 10)
    fake_excel(monkeypatch, {}, [["calc", "D20", "no value (circular reference)"], ["calc", "E20", "no value"]])
    rep = check.check_model(m.root)
    by = {f.cell: f for f in rep.findings if f.rule == "V-NO-VALUE"}
    assert "find the cycle" in by["D20"].fix and "break it" in by["D20"].fix
    assert "break it" not in by["E20"].fix and "find the cycle" not in by["E20"].fix
    assert by["E20"].fix.startswith("Excel saved this formula without a value and named no circular reference for "
                                    "it: recalculate the workbook")
    assert "trace" in by["E20"].fix

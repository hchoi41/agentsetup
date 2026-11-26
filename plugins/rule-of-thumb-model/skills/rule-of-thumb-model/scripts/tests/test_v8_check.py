"""Tests of the v8 check changes (package P4 of the v8 plan). Owner: P4.

Entries:
- [A-aera-1]: when the manifest's landed digest differs from its workbook_digest (raw/<t>.csv was landed after
  rotm.py last wrote rows into the workbook, for example with land --force), check --accept-refresh accepts no
  changed rows, whatever the raw sheet holds: R-CHANGED says that raw/<t>.csv was landed after the workbook was last
  written, and raw/<t>.csv, raw/<t>.controls.json and the manifest stay byte for byte. A manifest without
  workbook_digest behaves as in v7.
- [A-aera-5, A-aera-6] part (1): check (after the recalculation, and on the saved values without Excel) reads each
  scalar input whose specification declares "valid" (min and max, or list) from the workbook; a saved value outside
  it is V-INPUT-RANGE (an error) naming the cell, the value, the range and the input's note. No row is added to the
  checks sheet. The audit side (audit with a manifest) is tested here too, in the Excel test the plan names, and in
  test_v8_audit.py.
Tests marked excel drive Excel through lib.excel. The names of the first tests of [A-aera-5, A-aera-6] are fixed by
the v8 plan (section 5.3) and quoted by the documents.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from lib import audit, build, check, common, controls, land, manifest, spec as specmod
import test_check as tc
from test_v4_check import _pq_landing, _rows, _total, _write_rows, starter
from test_v5_check import EXAMPLE, example_copy

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]

LANDED_AFTER = "was landed after the workbook was last written"


def rules(rep) -> list[str]:
    return [f.rule for f in rep.findings]


def lines(rep) -> list[str]:
    return [f.line() for f in rep.findings] + list(rep.notes)


def out(r) -> str:
    return ((r.stdout or "") + (r.stderr or ""))[-6000:]


def three_files(root: Path, table: str) -> dict[str, bytes | None]:
    """raw/<t>.csv, raw/<t>.controls.json (None when absent) and the manifest, byte for byte."""
    paths = [root / "raw" / f"{table}.csv", root / "raw" / f"{table}.controls.json", root / "model.manifest.json"]
    return {p.name: (p.read_bytes() if p.is_file() else None) for p in paths}


def shipped_models() -> list[Path]:
    """The six shipped model folders that are present (the skill folder copied alone has no plugin around it)."""
    parents = [SKILL / "examples", SKILL / "templates"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        parents.append(PLUGIN / "templates")
    return [d for parent in parents if parent.is_dir() for d in sorted(parent.iterdir())
            if (d / "model.spec.json").is_file() and (d / "model.xlsx").is_file()]


SHIPPED = shipped_models()


# ============================================================================= [A-aera-1]

def _shift(rows: list[dict], delta: int) -> list[dict]:
    """The same rows with the amount of the first row raised by `delta` and of the second lowered by it: the row
    count and the amount total stay the same, the rows differ."""
    out_rows = [dict(r) for r in rows]
    for i, sign in ((0, 1), (1, -1)):
        out_rows[i]["amount"] = str(int(float(out_rows[i]["amount"])) + sign * delta)
    return out_rows


@pytest.mark.excel
def test_acceptance_after_a_later_landing_is_refused(tmp_path: Path) -> None:
    """The reviewer's sequence (r7 A-aera-1): a refresh that is not accepted, then land --force of another CSV with
    the same row count and total, then check --accept-refresh --control --strict. v7 exited 0 with R-ACCEPTED and
    wrote the older rows over the newer snapshot."""
    root = starter(tmp_path / "m", spec_edit=_pq_landing)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data["power_query_attached"] == ["lines"], lines(rep)
    raw_csv = root / "raw" / "lines.csv"                  # the file the generated query reads
    base = _rows(raw_csv)
    total = _total(base)
    _write_rows(raw_csv, _shift(base, 10))                # the source changed
    rep = check.check_model(root, refresh=True)
    assert rep.data["refreshed"] is True and "R-ACCEPTED" not in rules(rep), lines(rep)
    newer = _shift(base, 20)                              # another extract: same row count and total
    assert _total(newer) == total
    lrep = land.land_csv(root, "lines", _write_rows(tmp_path / "newer" / "lines.csv", newer), force=True)
    assert lrep.exit_code == 0, [f.line() for f in lrep.findings]
    before = three_files(root, "lines")
    r = tc.run_cli("check", str(root), "--accept-refresh", "--control", f"lines.amount={total}", "--strict")
    assert r.returncode != 0, out(r)
    assert LANDED_AFTER in out(r) and "R-CHANGED" in out(r), out(r)
    assert "R-ACCEPTED" not in out(r), out(r)
    assert three_files(root, "lines") == before


def test_acceptance_with_a_manifest_digest_mismatch_accepts_nothing_without_excel(tmp_path: Path,
                                                                                 monkeypatch) -> None:
    """The same rule without Excel: a query table (attach_query_table stands in), rows a refresh saved (openpyxl
    stands in), then land --force of other rows, so the manifest's digest differs from its workbook_digest."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    rows = tc.sales_values(12)
    rows[3][4] = 999.0
    # v9 (r8 verdict A-aera-4): --control needs a control total named by the contract; the contract's total is the
    # one of the later landing, so that land --force lands it without --control
    m = tc.make_model(tmp_path, 10, capacity=12, total=sum(r[4] for r in rows), landing="power_query",
                      query_table=True)
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))                 # rows a refresh saved, not accepted
    assert land.land_csv(m.root, "sales", tc.write_source_csv(tmp_path / "later.csv", 12, rows=rows),
                         force=True).exit_code == 0
    entry = manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")
    assert entry["digest"] != entry["workbook_digest"]
    before = three_files(m.root, "sales")
    total = sum(r[4] for r in tc.sales_values(12))
    rep = check.check_model(m.root, accept_refresh=True, strict=True, controls={"sales.amount": total},
                            control_texts=[f"sales.amount={total}"])
    assert rep.exit_code == 1 and "R-ACCEPTED" not in rules(rep), lines(rep)
    f = next(x for x in rep.findings if x.rule == "R-CHANGED")
    assert f.sheet == "raw_sales" and f.message == (
        "raw_sales: raw/sales.csv was landed after the workbook was last written (the manifest's landed digest "
        "differs from its workbook digest), so no changed rows are accepted; nothing was accepted, and "
        "raw/sales.csv, raw/sales.controls.json and the manifest were not changed")
    assert f.fix.startswith("rebuild from the landed snapshot (") and "build" in f.fix and "--force" in f.fix
    assert f"check \"{m.root.as_posix()}\" --refresh --strict" in f.fix and f.fix.endswith("(--accept-refresh)")
    assert three_files(m.root, "sales") == before and before["sales.controls.json"] is None
    # whatever the sheet holds: rows equal to neither the landing nor the workbook digest are refused the same way
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(11))
    rep = check.check_model(m.root, accept_refresh=True)
    assert "R-ACCEPTED" not in rules(rep)
    assert any(x.rule == "R-CHANGED" and LANDED_AFTER in x.message for x in rep.findings), lines(rep)
    assert three_files(m.root, "sales") == before


def test_check_without_acceptance_keeps_the_newer_text(tmp_path: Path, monkeypatch) -> None:
    """Without --accept-refresh the case v3 named (the sheet holds the rows rotm.py last wrote, raw/<t>.csv holds
    rows landed after them) keeps its own text."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, landing="power_query", query_table=True)
    rows = tc.sales_values(10)
    rows[3][4] = 999.0
    assert land.land_csv(m.root, "sales", tc.write_source_csv(tmp_path / "later.csv", 10, rows=rows),
                         force=True).exit_code == 0
    rep = check.check_model(m.root)
    f = next(x for x in rep.findings if x.rule == "R-CHANGED")
    assert "holds the rows the workbook had when rotm.py last wrote it" in f.message
    assert LANDED_AFTER not in f.message


def test_acceptance_after_a_rebuild_from_the_landed_snapshot_is_accepted(tmp_path: Path, monkeypatch) -> None:
    """The fix the refusal gives works: once the digests agree again (the rows rotm.py last wrote are the landed
    snapshot), a refresh is accepted as in v7."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, capacity=12, landing="power_query", query_table=True)
    man = manifest.load_manifest(m.wb)
    assert manifest.raw_sheet(man, "sales")["digest"] == manifest.raw_sheet(man, "sales")["workbook_digest"]
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    rep = check.check_model(m.root, accept_refresh=True)
    assert "R-ACCEPTED" in rules(rep), lines(rep)


def test_acceptance_without_a_workbook_digest_behaves_as_in_v7(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, capacity=12, landing="power_query", query_table=True)
    man = manifest.load_manifest(m.wb)
    manifest.raw_sheet(man, "sales").pop("workbook_digest", None)
    manifest.write_manifest(m.wb, man)
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    rep = check.check_model(m.root, accept_refresh=True)
    f = next(x for x in rep.findings if x.rule == "R-CHANGED")
    assert "records no workbook digest for raw_sales (built before v3)" in f.message
    assert LANDED_AFTER not in f.message


# ============================================================================= [A-aera-5, A-aera-6] part (1)

def test_valid_problem_gives_the_texts_of_the_build_check() -> None:
    vp = specmod.valid_problem
    assert vp({"min": 1000, "max": 1000}, 1000) is None and vp({"min": 1000, "max": 1000}, 1000.0) is None
    assert vp({"min": 1000, "max": 1000}, 1) == "outside its valid range 1000 to 1000"
    assert vp({"min": 0}, -1) == "outside its valid range of at least 0"
    assert vp({"max": 12}, 13) == "outside its valid range of at most 12"
    assert vp({"min": 0, "max": 1}, None) == "outside its valid range 0 to 1"            # an empty cell
    assert vp({"min": 0, "max": 1}, "0.5") == "outside its valid range 0 to 1"           # a number stored as text
    assert vp({"min": 0, "max": 1}, True) == "outside its valid range 0 to 1"
    assert vp({"list": ["A", "B"]}, "B") is None
    assert vp({"list": ["A", "B"]}, "C") == "which is not one of its valid values (A, B)"
    assert vp({"list": [1, 2]}, 2.0) is None and vp({"list": [1, 2]}, None) == "which is not one of its valid values (1, 2)"
    assert vp(None, 5) is None and vp({}, 5) is None


def _serial(d: dt.date) -> int:
    return (d - dt.date(1899, 12, 30)).days


def _set_cell(wb: Path, sheet: str, cell: str, value) -> None:
    import openpyxl
    book = openpyxl.load_workbook(wb)
    book[sheet][cell] = value
    book.save(wb)


def test_input_range_reads_a_date_input_by_its_serial(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    lo, hi = _serial(dt.date(2026, 1, 31)), _serial(dt.date(2026, 12, 31))

    def edit(spec: dict) -> None:
        items = next(s for s in spec["sheets"] if s["type"] == "inputs")["items"]
        next(i for i in items if i["id"] == "start_month")["valid"] = {"min": lo, "max": hi}

    root = starter(tmp_path / "m", spec_edit=edit)
    assert build.build(root / "model.spec.json", power_query=False).counts()["errors"] == 0
    wb = root / "model.xlsx"
    ref = manifest.load_manifest(wb)["inputs"]["start_month"]
    sheet, cell = common.parse_ref(ref)
    rep = check.check_model(root)
    assert "V-INPUT-RANGE" not in rules(rep), lines(rep)
    _set_cell(wb, sheet, cell, dt.datetime(2026, 12, 31))          # the last valid day, as a date cell
    assert "V-INPUT-RANGE" not in rules(check.check_model(root))
    _set_cell(wb, sheet, cell, dt.datetime(2027, 1, 31))
    rep = check.check_model(root)
    f = next(x for x in rep.findings if x.rule == "V-INPUT-RANGE")
    assert f.severity == "error" and (f.sheet, f.cell) == (sheet, cell) and rep.exit_code == 1
    assert f.message == (f"input 'start_month' (First month of the model) at {sheet}!{cell} holds 2027-01-31, outside "
                         f"its valid range {lo} to {hi}")
    arep = audit.audit_workbook(wb, record=False)
    a = next(x for x in arep.findings if x.rule == "V-INPUT-RANGE")
    assert (a.message, a.sheet, a.cell, a.severity) == (f.message, sheet, cell, "error")
    _set_cell(wb, sheet, cell, float(hi))                          # a serial typed as a number is read as one
    assert "V-INPUT-RANGE" not in rules(check.check_model(root))


def test_empty_input_cell_and_a_list_value_are_reported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")

    def edit(spec: dict) -> None:
        items = next(s for s in spec["sheets"] if s["type"] == "inputs")["items"]
        items.append({"id": "basis", "label": "Basis", "value": "cash", "type": "text", "source": "test",
                      "valid": {"list": ["cash", "accrual"]}, "note": "Agreed with the controller."})

    root = starter(tmp_path / "m", spec_edit=edit)
    assert build.build(root / "model.spec.json", power_query=False).counts()["errors"] == 0
    wb = root / "model.xlsx"
    sheet, cell = common.parse_ref(manifest.load_manifest(wb)["inputs"]["basis"])
    _set_cell(wb, sheet, cell, "accrued")
    f = next(x for x in check.check_model(root).findings if x.rule == "V-INPUT-RANGE")
    assert f.message == (f"input 'basis' (Basis) at {sheet}!{cell} holds 'accrued', which is not one of its valid "
                         "values (cash, accrual); the input's note says: Agreed with the controller.")
    assert "change the input's value in model.spec.json and rebuild" in f.fix and "--force" in f.fix
    assert "is not kept by a rebuild" in f.fix
    _set_cell(wb, sheet, cell, None)
    f = next(x for x in check.check_model(root).findings if x.rule == "V-INPUT-RANGE")
    assert f"at {sheet}!{cell} holds no value (the cell is empty), which is not one of its valid values" in f.message


def test_input_range_adds_no_row_to_the_checks_sheet(tmp_path: Path, monkeypatch) -> None:
    """V-INPUT-RANGE is a finding of check and audit only: the built checks sheet and the manifest's check list
    are those of a specification without the rule (no check reads the input's range)."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter(tmp_path / "m")
    assert build.build(root / "model.spec.json", power_query=False).counts()["errors"] == 0
    man = manifest.load_manifest(root / "model.xlsx")
    assert not any("range" in str(c.get("id")) and "input" in str(c.get("id")) for c in man["checks"])
    assert all(c.get("id") != "V-INPUT-RANGE" for c in man["checks"])


@pytest.mark.excel
def test_saved_scale_outside_its_range_fails_check_and_audit(tmp_path: Path, monkeypatch) -> None:
    """r7 A-aera-5: the worked example saved by Excel with the scale cell inputs!D7 set to 1. v7: check --strict
    and audit --strict exited 0 with every check holding."""
    from lib import excel
    root = example_copy(tmp_path, monkeypatch)
    wb = root / "model.xlsx"
    assert manifest.load_manifest(wb)["inputs"]["scale"] == "inputs!D7"
    res = excel.apply_edits(wb, [{"op": "value", "sheet": "inputs", "cell": "D7", "value": 1}], out=wb)
    assert res.ok, res.message()
    spec = json.loads((root / "model.spec.json").read_text(encoding="utf-8"))
    note = next(i for s in spec["sheets"] if s["type"] == "inputs" for i in s["items"] if i["id"] == "scale")["note"]
    words = ["V-INPUT-RANGE", "inputs!D7", "holds 1,", "outside its valid range 1000 to 1000",
             f"the input's note says: {note}"]
    env = {"ROTM_EXAMPLE_ROOTS": str(tmp_path)}
    r = tc.run_cli("check", str(root), "--strict", env_extra=env)
    assert r.returncode == 1, out(r)
    flat = " ".join(out(r).split())
    assert all(w in flat for w in words), (words, out(r))
    r = tc.run_cli("audit", str(root), "--strict", "--no-record", env_extra=env)
    assert r.returncode == 1, out(r)
    flat = " ".join(out(r).split())
    assert all(w in flat for w in words), (words, out(r))


def test_shipped_models_give_no_input_range_finding(tmp_path: Path, monkeypatch) -> None:
    """The six shipped workbooks with their manifests: audit (read only, nothing recorded) and check without Excel
    on a copy of each folder (check writes reports/) give no V-INPUT-RANGE."""
    assert len(SHIPPED) >= 2, SHIPPED
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    monkeypatch.setenv("ROTM_EXAMPLE_ROOTS", str(tmp_path))
    checked = 0
    for folder in SHIPPED:
        rep = audit.audit_workbook(folder / "model.xlsx", record=False)
        assert "V-INPUT-RANGE" not in rules(rep), (folder.name, lines(rep))
        copy = tmp_path / folder.parent.name / folder.name
        shutil.copytree(folder, copy, ignore=shutil.ignore_patterns("reports", "*.bak_*"))
        rep = check.check_model(copy)
        assert "V-INPUT-RANGE" not in rules(rep), (folder.name, lines(rep))
        spec = json.loads((folder / "model.spec.json").read_text(encoding="utf-8"))
        checked += sum(1 for s in spec["sheets"] if s.get("type") == "inputs" for i in s.get("items") or []
                       if i.get("kind") in (None, "scalar") and isinstance(i.get("valid"), dict))
    assert checked >= len(SHIPPED) - 1                 # every shipped model but the starter has a fixed scale


def test_check_rule_list_names_v_input_range() -> None:
    sev, what = check.RULES["V-INPUT-RANGE"]
    assert sev == "error" and "valid" in what

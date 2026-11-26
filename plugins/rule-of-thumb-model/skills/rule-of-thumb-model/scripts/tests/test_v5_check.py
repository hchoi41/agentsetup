"""Tests of the v5 check changes (package P3 of the v5 plan). Owner: P3.

Entries: [A-aera-2, C-sol-1, C-sol-4] parts 1 and 2 (a refresh counts only when a query table was refreshed;
X-NO-QUERY for a raw table the manifest records as loaded by Power Query whose sheet holds no query table; the query
file compared with the text the attach recorded, query_body_sha256, before Excel starts) and [A-aera-3, C-sol-5]
parts 2 and 3 (rule C-RECOMPUTE, lib/recompute.py). Tests marked excel drive Excel through lib.excel. Tests that
need another package's change (v5 plan section 7): I-4 (P5's rule entry and the integrator's audit call), I-5
(P4's query_body_sha256 at the attach), I-6 (P2's result_formula and master_formula).
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import build, check, common, land, manifest, recompute
import test_check as tc
from test_v4_check import _built_starter

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
EXAMPLE = SKILL / "examples" / "sales_vs_target"
SHIPPED = [EXAMPLE, SKILL / "templates" / "starter"] + [
    PLUGIN / "templates" / n for n in ("ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast")]


# ----------------------------------------------------------------------------- helpers

def rules(rep) -> list[str]:
    return [f.rule for f in rep.findings]


def lines(rep) -> list[str]:
    return [f.line() for f in rep.findings] + list(rep.notes)


def _bytes(root: Path) -> dict[str, bytes]:
    """The workbook, the manifest and every file under raw/, by relative path."""
    paths = [root / "model.xlsx", root / "model.manifest.json"] + sorted((root / "raw").rglob("*"))
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in paths if p.is_file()}


def _set_query_fields(root: Path, table: str = "lines", *, body_hash: bool = True) -> dict:
    """The raw entry recorded as `land --power-query` records it (v5: with query_body_sha256 of the current file
    when `body_hash`), without Excel: the workbook keeps its static raw sheet (no query table)."""
    wb = root / "model.xlsx"
    man = manifest.load_manifest(wb)
    entry = manifest.raw_sheet(man, table)
    agreed = common.read_json(root / "contracts" / f"{table}.contract.json")["agreement"]["contract_sha256"]
    entry.update(landing="power_query", query_name=f"q_{table}", query_contract_sha256=agreed)
    qfile = root / "queries" / f"{table}.pq.m"
    assert qfile.is_file(), "land writes queries/<t>.pq.m"
    if body_hash:
        entry["query_body_sha256"] = check._query_body_sha256(qfile.read_text(encoding="utf-8"))
    else:
        entry.pop("query_body_sha256", None)
    manifest.write_manifest(wb, man)
    return entry


def _ok_job_without_tables(monkeypatch) -> list[list[dict]]:
    """check._excel_job as if Excel had recalculated and its pq_refresh step had refreshed no table; returns the list
    the operations of each job are appended to."""
    from lib import excel
    monkeypatch.delenv("ROTM_NO_EXCEL", raising=False)
    monkeypatch.setattr(excel, "excel_status", lambda: SimpleNamespace(available=True, reason=""))
    calls: list[list[dict]] = []

    def job(wb, ops, read_refs, timeout_s=None):
        calls.append(list(ops))
        steps = [{"op": "pq_refresh", "ok": True, "tables": [], "connections": []}] if any(
            o.get("op") == "pq_refresh" for o in ops) else []
        res = excel.JobResult(ok=True, pid=4242, steps=steps + [{"op": "recalc", "ok": True}], excel_version="16.0")
        return res, {ref: 0 for ref in read_refs}, [], 0
    monkeypatch.setattr(check, "_excel_job", job)
    return calls


def _sheet_part(z: zipfile.ZipFile, sheet: str) -> str:
    book = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rid = re.search(rf'<sheet [^>]*name="{re.escape(sheet)}"[^>]*r:id="([^"]+)"', book).group(1)
    target = re.search(rf'<Relationship [^>]*Id="{rid}"[^>]*Target="([^"]+)"', rels)
    if target is None:
        target = re.search(rf'<Relationship [^>]*Target="([^"]+)"[^>]*Id="{rid}"', rels)
    t = target.group(1).lstrip("/")
    return t if t.startswith("xl/") else "xl/" + t


def patch_cells(path: Path, sheet: str, cells: dict[str, tuple[str | None, str, str | None]]) -> None:
    """Rewrite cells in the saved XML: coord -> (formula or None, cached value text, cell type or None (a number)).
    The style of each cell is kept; every other part of the package stays as it is."""
    with zipfile.ZipFile(path) as z:
        part = _sheet_part(z, sheet)
        items = {n: z.read(n) for n in z.namelist()}
    xml = items[part].decode("utf-8")
    for coord, (formula, value, typ) in cells.items():
        pat = re.compile(rf'<c r="{coord}"(?P<attrs>[^>]*?)(?:/>|>.*?</c>)', re.S)
        m = pat.search(xml)
        assert m, f"{sheet}!{coord} not found in {part}"
        style = re.search(r'\ss="(\d+)"', m.group("attrs"))
        attrs = f' r="{coord}"' + (f' s="{style.group(1)}"' if style else "") + (f' t="{typ}"' if typ else "")
        body = (f"<f>{html.escape(formula, quote=False)}</f>" if formula is not None else "") + \
            f"<v>{html.escape(value, quote=False)}</v>"
        xml = xml[:m.start()] + f"<c{attrs}>{body}</c>" + xml[m.end():]
    items[part] = xml.encode("utf-8")
    tmp = path.with_name(path.name + ".partial")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in items.items():
            z.writestr(n, data)
    tmp.replace(path)


def example_copy(tmp_path: Path, monkeypatch=None) -> Path:
    """A copy of the shipped worked example, whose model.xlsx holds Excel's cached values; its contracts carry the
    'example' agreement, accepted for this copy through ROTM_EXAMPLE_ROOTS."""
    if not (EXAMPLE / "model.xlsx").is_file():
        pytest.skip("the worked example's model.xlsx is absent")
    root = tmp_path / "example"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("*.bak_*", "reports"))
    if monkeypatch is not None:
        monkeypatch.setenv("ROTM_EXAMPLE_ROOTS", str(tmp_path))
    return root


def without_formula_fields(man: dict) -> dict:
    """The manifest without result_formula and master_formula (part 2 of the rule only), so a test of the
    recomputation gives the same findings before and after the shipped folders are built with those fields."""
    for c in man.get("checks") or []:
        c.pop("result_formula", None)
    man.pop("master_formula", None)
    return man


def recompute_hits(findings, cell: str) -> list:
    """The C-RECOMPUTE findings, after asserting that each names `cell` and that one of them is message (a)."""
    hits = [f for f in findings if (f.rule if hasattr(f, "rule") else f["rule"]) == "C-RECOMPUTE"]
    cells = {(f.cell if hasattr(f, "cell") else f["cell"]) for f in hits}
    assert cells == {cell}, [getattr(f, "message", f) for f in hits]
    return hits


def zero_result_with_differing_operands(root: Path) -> None:
    """checks row 6 (sales_total_amount, kind equal, tolerance 0): its expected side 350 below its actual side
    (E6 = inputs!$D$21-350, cached 264697000, G6 cached 350), and its Result replaced by =0 (cached 0)."""
    patch_cells(root / "model.xlsx", "checks", {"E6": ("inputs!$D$21-350", "264697000", None),
                                               "G6": ("D6-E6", "350", None),
                                               "I6": ("0", "0", None)})


# ============================================================================= 1. [A-aera-2, C-sol-1, C-sol-4] part 1

def test_power_query_manifest_without_a_query_table_is_x_no_query(tmp_path: Path, monkeypatch) -> None:
    root = _built_starter(tmp_path)
    _set_query_fields(root)
    assert "raw_lines" not in check.query_tables(root / "model.xlsx")
    before = _bytes(root)
    _ok_job_without_tables(monkeypatch)
    rep = check.check_model(root, strict=True, refresh=True)
    assert rep.exit_code != 0, lines(rep)
    hits = [f for f in rep.findings if f.rule == "X-NO-QUERY"]
    assert len(hits) == 1 and hits[0].severity == "warning", lines(rep)
    assert hits[0].sheet == "raw_lines" and hits[0].message.startswith(
        "raw_lines: the manifest records that a Power Query query loads it, but the workbook holds no query table at "
        "A1 of raw_lines; it was not refreshed and nothing was accepted for it"), hits[0].message
    assert "--table lines --power-query" in hits[0].fix and "--refresh" in hits[0].fix
    assert rep.data["refreshed"] is False
    info = rep.data["tables"]["lines"]
    assert info["query_loaded"] is False and info["query_table"] is False, info
    assert not info.get("accepted")
    assert _bytes(root) == before
    # with --accept-refresh the same finding is an error, and still nothing is accepted
    rep = check.check_model(root, refresh=True, accept_refresh=True)
    hit = next(f for f in rep.findings if f.rule == "X-NO-QUERY")
    assert hit.severity == "error" and rep.exit_code == 1 and rep.data["refreshed"] is False
    assert _bytes(root) == before


def test_refresh_step_that_refreshed_no_table_is_not_a_refresh(tmp_path: Path, monkeypatch) -> None:
    """The refresh step ran (the workbook holds a query connection) but reported no refreshed table: refreshed is
    false and nothing is accepted."""
    m = tc.make_model(tmp_path, 10, capacity=12, landing="power_query", query_table=True)
    calls = _ok_job_without_tables(monkeypatch)
    rep = check.check_model(m.root, refresh=True, accept_refresh=True)
    assert any(o.get("op") == "pq_refresh" for o in calls[0]), calls
    assert rep.data["refreshed"] is False, lines(rep)
    assert any("no table was accepted: --refresh was asked for and no refresh ran" in n for n in rep.notes), lines(rep)


@pytest.mark.excel
def test_power_query_manifest_without_a_query_table_is_x_no_query_in_excel(tmp_path: Path) -> None:
    root = _built_starter(tmp_path)
    _set_query_fields(root)
    before = _bytes(root)
    digest_before = manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "lines").get("digest")
    rep = check.check_model(root, strict=True, refresh=True)
    assert rep.exit_code == 1, lines(rep)
    hit = next(f for f in rep.findings if f.rule == "X-NO-QUERY")
    assert hit.sheet == "raw_lines" and "raw_lines" in hit.message
    assert rep.data["refreshed"] is False and rep.data["recalculated"] is True
    assert rep.data["tables"]["lines"]["query_loaded"] is False
    after = _bytes(root)
    # the check run recalculates and saves the workbook (as every check with Excel does); its raw rows, raw/ and
    # the manifest stay as they were
    assert {k: v for k, v in after.items() if k != "model.xlsx"} == {k: v for k, v in before.items()
                                                                     if k != "model.xlsx"}
    assert rep.data["tables"]["lines"]["digest"] == digest_before


# ============================================================================= 2. [A-aera-2, C-sol-1, C-sol-4] part 2

def test_query_body_hash_is_the_stamp_body_hash() -> None:
    text = "let\r\n  Source = 1\r\nin\r\n  Source\r\n"
    stamped = common.stamp_query(text, "a" * 64, "//")
    assert check._query_body_sha256(stamped) == common.read_query_stamp(stamped)["body_sha256"]
    assert check._query_body_sha256(text) == check._query_body_sha256(stamped)
    assert check._query_body_sha256("﻿" + stamped) == check._query_body_sha256(stamped)


def test_refresh_refusal_for_a_changed_query_file_without_excel(tmp_path: Path) -> None:
    root = _built_starter(tmp_path)
    _set_query_fields(root)
    env = {"ROTM_NO_EXCEL": "1"}
    r = tc.run_cli("check", str(root), "--refresh", env_extra=env)
    out = r.stdout + r.stderr
    assert r.returncode != 2 and "differs from the query text attached" not in out, out[-3000:]
    qfile = root / "queries" / "lines.pq.m"
    qfile.write_text(qfile.read_text(encoding="utf-8") + "// edited by hand after the attach\n", encoding="utf-8")
    r = tc.run_cli("land", str(root), "--table", "lines", "--keep-queries", env_extra=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    before = _bytes(root)
    r = tc.run_cli("check", str(root), "--refresh", env_extra=env)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out[-3000:]
    assert "raw_lines: queries/lines.pq.m differs from the query text attached to the workbook" in out, out[-3000:]
    assert "--table lines --power-query" in out and "nothing was refreshed" in out
    assert _bytes(root) == before


def test_refresh_refusal_for_a_missing_query_file(tmp_path: Path) -> None:
    root = _built_starter(tmp_path)
    _set_query_fields(root)
    (root / "queries" / "lines.pq.m").unlink()
    with pytest.raises(common.Refusal) as ei:
        check.check_model(root, refresh=True, excel=False)
    text = ei.value.line() + " " + " ".join(ei.value.details)
    assert ("raw_lines: queries/lines.pq.m is missing, so it cannot be compared with the query the workbook holds; "
            "fix: ") in text and "--table lines --power-query" in text


def test_refresh_without_a_recorded_query_hash_gets_a_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _built_starter(tmp_path)
    _set_query_fields(root, body_hash=False)
    qfile = root / "queries" / "lines.pq.m"
    qfile.write_text(qfile.read_text(encoding="utf-8") + "// edited\n", encoding="utf-8")
    rep = check.check_model(root, refresh=True)                     # not refused
    note = next(n for n in rep.notes if "records no hash of the attached query text" in n)
    assert note.startswith("--refresh: raw_lines's manifest records no hash of the attached query text (attached "
                           "before v5), so queries/lines.pq.m was not compared with the workbook's query; "), note
    assert note.endswith("--table lines --power-query attaches it again and records the hash")


def test_hand_attached_query_is_not_compared(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _built_starter(tmp_path)
    entry = _set_query_fields(root)
    man = manifest.load_manifest(root / "model.xlsx")
    manifest.raw_sheet(man, "lines").update(query_attach="hand", query_body_sha256="0" * 64)
    manifest.write_manifest(root / "model.xlsx", man)
    assert entry["query_body_sha256"] != "0" * 64
    rep = check.check_model(root, refresh=True)                     # not refused
    assert any("was attached by hand" in n for n in rep.notes), lines(rep)
    assert not any("records no hash of the attached query text" in n for n in rep.notes)


@pytest.mark.excel
def test_refresh_is_refused_when_the_query_file_differs_from_the_attached_text(tmp_path: Path) -> None:
    """I-5: needs P4's query_body_sha256 written by land --power-query."""
    root = _built_starter(tmp_path)
    r = tc.run_cli("land", str(root), "--table", "lines", "--power-query")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    entry = manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "lines")
    qfile = root / "queries" / "lines.pq.m"
    assert entry.get("query_body_sha256") == check._query_body_sha256(qfile.read_text(encoding="utf-8")), entry
    qfile.write_text(qfile.read_text(encoding="utf-8") + "// edited by hand after the attach\n", encoding="utf-8")
    r = tc.run_cli("land", str(root), "--table", "lines", "--keep-queries")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    before = _bytes(root)
    r = tc.run_cli("check", str(root), "--refresh")
    out = r.stdout + r.stderr
    assert r.returncode == 2 and "raw_lines: queries/lines.pq.m differs from the query text attached" in out, \
        out[-3000:]
    assert "--table lines --power-query" in out
    assert _bytes(root) == before
    r = tc.run_cli("land", str(root), "--table", "lines", "--power-query")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    rep = check.check_model(root, refresh=True)
    assert rep.data["refreshed"] is True, lines(rep)
    text = qfile.read_text(encoding="utf-8")
    assert check._query_body_sha256(text) == land.query_body_sha256(text)


# ============================================================================= 3. [A-aera-3, C-sol-5] parts 2 and 3

def test_recompute_constants_equal_build_chk() -> None:
    assert recompute.CHK == build.CHK
    assert recompute.CHECK_FIRST_ROW == build.CHECK_FIRST_ROW


def test_result_replaced_by_zero_is_c_recompute_without_excel(tmp_path: Path) -> None:
    root = example_copy(tmp_path)
    man = without_formula_fields(manifest.load_manifest(root / "model.xlsx"))
    assert recompute.check_findings(root / "model.xlsx", man) == []
    zero_result_with_differing_operands(root)
    found = recompute.check_findings(root / "model.xlsx", man)
    assert [(f.rule, f.severity, f.sheet, f.cell) for f in found] == [("C-RECOMPUTE", "error", "checks", "I6")], \
        [f.line() for f in found]
    assert found[0].message == ("check 'sales_total_amount' (checks!I6): the saved result is 0, but its operands give "
                                "1: |264697350 - 264697000| = 350, tolerance 0"), found[0].message
    assert found[0].fix.startswith("rebuild the workbook with ") and "build \"" in found[0].fix
    assert "--force (build writes the check formulas again), then run " in found[0].fix


def test_result_replaced_by_zero_is_c_recompute_in_check_without_excel(tmp_path: Path, monkeypatch) -> None:
    """check_model calls lib.recompute after a completed recalculation (here a job that reports one)."""
    from test_v2_raw_layer import fake_excel
    root = example_copy(tmp_path, monkeypatch)
    zero_result_with_differing_operands(root)
    fake_excel(monkeypatch, {})
    rep = check.check_model(root, strict=True)
    hits = recompute_hits(rep.findings, "I6")
    assert any(f.message.startswith("check 'sales_total_amount' (checks!I6): the saved result is 0, but its operands "
                                    "give 1") for f in hits), lines(rep)
    assert all(f.sheet == "checks" for f in hits) and rep.exit_code == 1
    # without a recalculation, check does not recompute (the saved values are not verified)
    rep = check.check_model(root, strict=True, excel=False)
    assert "C-RECOMPUTE" not in rules(rep)


@pytest.mark.excel
def test_result_replaced_by_zero_is_c_recompute_in_check(tmp_path: Path, monkeypatch) -> None:
    root = example_copy(tmp_path)
    zero_result_with_differing_operands(root)
    r = tc.run_cli("check", str(root), "--strict", env_extra={"ROTM_EXAMPLE_ROOTS": str(tmp_path)})
    out = r.stdout + r.stderr
    assert r.returncode == 1, out[-3000:]
    assert "C-RECOMPUTE" in out and "check 'sales_total_amount' (checks!I6): the saved result is 0" in out, out[-3000:]


def test_result_replaced_by_zero_is_c_recompute_in_audit(tmp_path: Path) -> None:
    """I-4: needs P5's rule entry C-RECOMPUTE and the integrator's call in audit._check_rules."""
    root = example_copy(tmp_path)
    zero_result_with_differing_operands(root)
    cmd = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "audit", str(root), "--strict",
           "--json", "-", "--quiet", "--no-record"]
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600,
                          env={**__import__("os").environ, "ROTM_EXAMPLE_ROOTS": str(tmp_path)})
    assert proc.returncode == 1 and "Traceback" not in proc.stderr, proc.stderr[-3000:]
    hits = recompute_hits(json.loads(proc.stdout)["findings"], "I6")
    assert all(f["sheet"] == "checks" for f in hits)
    assert any("check 'sales_total_amount' (checks!I6): the saved result is 0, but its operands give 1" in f["message"]
               for f in hits), hits


def test_text_operand_is_c_recompute(tmp_path: Path) -> None:
    root = example_copy(tmp_path)
    man = without_formula_fields(manifest.load_manifest(root / "model.xlsx"))
    # the expected side of sales_total_amount holds the number as text; the tolerance of variance_ties is text too
    patch_cells(root / "model.xlsx", "checks", {"E6": ("inputs!$D$21", "264697350", "str"),
                                               "H23": ("inputs!$D$26", "0.001", "str")})
    found = recompute.check_findings(root / "model.xlsx", man)
    got = {(f.cell, f.message) for f in found}
    assert ("E6", "check 'sales_total_amount' (checks!I6): expected checks!E6 holds '264697350', not a number") in got, \
        got
    assert ("H23", "check 'variance_ties' (checks!I23): tolerance checks!H23 holds '0.001', not a number") in got, got
    assert len(found) == 2 and all(f.rule == "C-RECOMPUTE" and f.severity == "error" for f in found)


def test_master_differing_from_the_results_is_c_recompute(tmp_path: Path) -> None:
    root = example_copy(tmp_path)
    man = without_formula_fields(manifest.load_manifest(root / "model.xlsx"))
    patch_cells(root / "model.xlsx", "checks", {"C2": ("0", "0", None), "I7": ("--(D7<>0)", "1", None),
                                               "D7": ("1", "1", None)})
    found = recompute.check_findings(root / "model.xlsx", man)
    assert [f.message for f in found] == ["the master check checks!C2 holds 0, but the check results give 1"], \
        [f.line() for f in found]
    assert found[0].cell == "C2"


def test_changed_result_formula_is_c_recompute(tmp_path: Path) -> None:
    root = example_copy(tmp_path)
    man = without_formula_fields(manifest.load_manifest(root / "model.xlsx"))
    entry = next(c for c in man["checks"] if c["id"] == "sales_total_amount")
    entry["result_formula"] = "=--(ABS(G6)>H6)"
    # v7 integrator: the last check row follows the worked example's error checks (26 in v7: rows 5 to 30)
    last = 4 + len([c for c in man["checks"] if c.get("severity", "error") == "error"])
    man["master_formula"] = f"=--(OR(SUM(I5:I{last})>0,COUNT(I5:I{last})<>COUNTA(B5:B{last})))"
    assert recompute.check_findings(root / "model.xlsx", man) == []
    # the Result formula changed (its saved value still follows from its operands), the master formula too
    patch_cells(root / "model.xlsx", "checks", {"I6": ("--(ABS(G6)>H6+1)", "0", None),
                                               "C2": (f"--(SUM(I5:I{last})>0)", "0", None)})
    found = recompute.check_findings(root / "model.xlsx", man)
    msgs = sorted(f.message for f in found)
    assert msgs == sorted([
        f"check 'sales_total_amount' (checks!I6): its formula =--(ABS(G6)>H6+1) differs from the formula build wrote "
        f"({entry['result_formula']})",
        f"the master check checks!C2: its formula =--(SUM(I5:I{last})>0) differs from the formula build wrote "
        f"({man['master_formula']})"]), msgs
    # a manifest without the fields: the recomputation only (the saved values agree), no finding
    plain = without_formula_fields(manifest.load_manifest(root / "model.xlsx"))
    assert recompute.check_findings(root / "model.xlsx", plain) == []


def test_built_manifest_formulas_are_compared(tmp_path: Path) -> None:
    """I-6: needs P2's result_formula (every check entry) and master_formula in the manifest build writes."""
    import openpyxl
    root = _built_starter(tmp_path)
    wb = root / "model.xlsx"
    man = manifest.load_manifest(wb)
    assert man.get("master_formula") and all(c.get("result_formula") for c in man["checks"] if c.get("result")), \
        "the built manifest holds no result_formula / master_formula (P2)"
    assert recompute.check_findings(wb, man) == []
    first = next(c for c in man["checks"] if c.get("result"))
    sheet, coord = common.parse_ref(first["result"])
    book = openpyxl.load_workbook(wb)
    book[sheet][coord] = "=0"
    book.save(wb)
    found = recompute.check_findings(wb, man)
    assert [(f.rule, f.cell) for f in found] == [("C-RECOMPUTE", coord)], [f.line() for f in found]
    assert found[0].message == (f"check '{first['id']}' ({sheet}!{coord}): its formula =0 differs from the formula "
                                f"build wrote ({first['result_formula']})")


@pytest.mark.parametrize("src", SHIPPED, ids=[p.name for p in SHIPPED])
def test_shipped_models_give_no_c_recompute(src: Path) -> None:
    if not src.is_dir() and src.parent == PLUGIN / "templates":
        pytest.skip(f"the plugin's templates folder is absent (skill copied alone): {src.name}")
    wb = src / "model.xlsx"
    if not wb.is_file():
        pytest.skip(f"{src.name} has no built model.xlsx")
    man = manifest.load_manifest(wb)
    import openpyxl
    values = openpyxl.load_workbook(wb, data_only=True, read_only=True)
    try:
        sheet, coord = common.parse_ref(man["checks"][0]["result"])
        assert values[sheet][coord].value is not None, f"{src.name}: the shipped workbook holds no cached values"
    finally:
        values.close()
    found = recompute.check_findings(wb, man)
    assert found == [], [f.line() for f in found]

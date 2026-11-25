"""Tests of the fixes made after live test 1 (2026-11-25, build/live/sample1). Owner: FIXER (Phase E).

The live session built a correct workbook, but showed these plugin defects, each tested here:
1. No supported way to land data that breaks its contract (a repeated key, an empty cell in a never-null
   column): the contract's known exceptions (expect.key_repeats, columns[i].known_nulls) keep the checks.
2. An agreement was not tied to a contract version: versions, agreed content in the history, the changes since
   the last agreed version, and reply_reused_from.
3. The audit's S-NUMFMT ignored a contract column's format: the manifest records the raw formats.
4. A number column showed no decimals (7.25 as 7): two decimals for a number column whose values hold a fraction.
5. The key check helper used COUNTIFS, which reads keys such as 00123, TRUE, =x and <5 as numbers, logicals or
   comparisons (measured in Excel 16). v2 (A-aera-9): one key equality everywhere (exact text, case-sensitive, no
   wildcard, text never read as a number): the dup helper is =--(SUMPRODUCT(--EXACT(K$2:K2,K2))>1)*B2.
6. A spec check had no format setting; 7. DATEVALUE was not in the function catalogue; 8. prose in a
   source_expression went into the SQL as it was.
"""
from __future__ import annotations

import copy
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data" / "build"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import audit, build, check, common, contract, functions, land, manifest, modelfolder, schemas  # noqa: E402
from test_build import agree, base_contract, base_spec, load, make_model  # noqa: E402

ROTM = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py")]


def _rows() -> tuple[list[str], list[list[str]]]:
    with open(DATA / "sales.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def _write_csv(path: Path, header: list[str], body: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(body)
    return path


def _defective_csv(tmp_path: Path, *, repeats: int = 1, blank_region: int = 1, fraction: bool = False) -> Path:
    """The fixture's 72 lines, with `repeats` lines delivered twice and `blank_region` regions left empty."""
    header, body = _rows()
    body = [list(r) for r in body]
    for i in range(blank_region):
        body[10 + i][3] = ""
    for i in range(repeats):
        body.append(list(body[20 + i]))
    if fraction:
        body[0][5] = "19250.25"
    return _write_csv(tmp_path / "defective.csv", header, body)


def _known_contract(*, repeats: int = 1, nulls: int = 1) -> dict:
    c = base_contract()
    c.pop("agreement", None)
    c["expect"] = {"rows": {"min": 1, "max": 1000}}
    if repeats:
        c["expect"]["key_repeats"] = {"max": repeats, "note": "lines delivered twice by the source"}
    for col in c["columns"]:
        if col["name"] == "region" and nulls:
            col["null_rule"] = "never"
            col["known_nulls"] = {"max": nulls, "note": "lines without a region"}
            col["allowed"] = None
    return c


def _sample_csv(tmp_path: Path, rows: int = 150) -> Path:
    header = ["txn_id", "region", "amount"]
    body = [[f"T{i:05d}", "East" if i % 2 else "West", f"{i}.25"] for i in range(1, rows + 1)]
    body[min(50, rows // 3)][1] = ""       # one empty region (under 1% of 150 rows)
    body.append(list(body[min(99, rows - 1)]))   # T00100 (or the last line) delivered twice
    return _write_csv(tmp_path / "sample.csv", header, body)


# ----------------------------------------------------------------------------- 1. known exceptions: propose

def test_propose_writes_known_exceptions_from_the_sample(tmp_path: Path) -> None:
    root = tmp_path / "m"
    path = contract.propose(root, "sales", csv=_sample_csv(tmp_path), key="txn_id", grain="one row per line",
                            rows="151", units=["amount=KRW"])
    data = common.read_json(path)
    assert data["version"] == 1
    assert data["key"] == ["txn_id"]
    assert data["expect"]["key_repeats"]["max"] == 1 and "T00100" in data["expect"]["key_repeats"]["note"]
    region = next(c for c in data["columns"] if c["name"] == "region")
    assert region["null_rule"] == "never" and region["known_nulls"]["max"] == 1
    notes = " ".join(data["notes"])
    assert "proposed as 1 known repeat" in notes and "empty cell(s) agreed as known" in notes
    md = (root / "contracts" / "sales.contract.md").read_text(encoding="utf-8")
    assert "except the known repeats below" in md and "Known exception: region is empty in at most 1" in md
    assert schemas.validate(data, "contract") == []


def test_propose_flags_and_the_explicit_allowed_rule(tmp_path: Path) -> None:
    root = tmp_path / "m"
    path = contract.propose(root, "sales", csv=_sample_csv(tmp_path), key="txn_id", grain="one row per line",
                            rows="151", key_repeats=3, nulls=["region=allowed"])
    data = common.read_json(path)
    assert data["expect"]["key_repeats"]["max"] == 3
    region = next(c for c in data["columns"] if c["name"] == "region")
    assert region["null_rule"] == "allowed" and "known_nulls" not in region
    assert any("--known-nulls region=1 instead" in n for n in data["notes"])
    path = contract.propose(root, "sales", csv=_sample_csv(tmp_path), key="txn_id", grain="one row per line",
                            rows="151", known_nulls=["region=2"], force=True)
    data = common.read_json(path)
    assert data["version"] == 2
    region = next(c for c in data["columns"] if c["name"] == "region")
    assert region["known_nulls"]["max"] == 2
    with pytest.raises(common.Refusal) as exc:
        contract.propose(root, "sales", csv=_sample_csv(tmp_path), key="txn_id", grain="g", rows="151",
                         known_nulls=["region=1"], nulls=["region=allowed"], force=True)
    assert "contradict" in exc.value.message


def test_a_small_sample_with_nulls_stays_allowed(tmp_path: Path) -> None:
    path = contract.propose(tmp_path / "m", "sales", csv=_sample_csv(tmp_path, rows=40), key="txn_id",
                            grain="one row per line", rows="41")
    region = next(c for c in common.read_json(path)["columns"] if c["name"] == "region")
    assert region["null_rule"] == "allowed" and "known_nulls" not in region


@pytest.mark.parametrize("where, why", [("first", "first column"), ("key", "key column"),
                                        ("allowed", "nothing to agree as known")])
def test_schema_refuses_known_nulls_where_they_cannot_apply(where: str, why: str) -> None:
    c = _known_contract()
    if where == "first":
        c["columns"][0]["known_nulls"] = {"max": 1}
    elif where == "key":
        c["columns"][1]["known_nulls"] = {"max": 1}
    else:
        region = next(x for x in c["columns"] if x["name"] == "region")
        region["null_rule"] = "allowed"
    errs = schemas.validate(c, "contract")
    assert any(why in e.message for e in errs), [e.line() for e in errs]


# ----------------------------------------------------------------------------- 1. known exceptions: land, check

def test_land_keeps_rows_within_the_known_exceptions(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m", contract=_known_contract(), land_it=False)
    rep = land.land_csv(root, "sales", _defective_csv(tmp_path))
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert sorted(f.rule for f in rep.findings) == ["L-KNOWN", "L-KNOWN"]
    assert sum(1 for _ in open(root / "raw" / "sales.csv", encoding="utf-8")) == 1 + 73


def test_land_refuses_more_defects_than_agreed(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m", contract=_known_contract(), land_it=False)
    rep = land.land_csv(root, "sales", _defective_csv(tmp_path, repeats=2, blank_region=2))
    rules = sorted(f.rule for f in rep.findings if f.severity == "error")
    assert rules == ["L-KEY", "L-NULL"]
    assert all("agrees only 1 as known" in f.message for f in rep.findings if f.severity == "error")
    assert not (root / "raw" / "sales.csv").exists()


def test_land_compares_text_keys_exactly(tmp_path: Path) -> None:
    # v2, A-aera-9: 000101A and 000101a are two keys (exact text, case-sensitive); land, check and the workbook's
    # dup column agree. This test needs P3's supporting change in land.py (land.key_tuple = common.key_tuple).
    header, body = _rows()
    body = [list(r) for r in body]
    body.append(list(body[0]))
    body[-1][0] = body[0][0].upper() + "A"
    body.append(list(body[-1]))
    body[-1][0] = body[-2][0][:-1] + "a"
    c = base_contract()
    c["expect"] = {"rows": {"min": 1, "max": 1000}}
    root = make_model(tmp_path / "m", contract=c, land_it=False)
    rep = land.land_csv(root, "sales", _write_csv(tmp_path / "case.csv", header, body))
    assert [f for f in rep.findings if f.rule == "L-KEY"] == [], [f.line() for f in rep.findings]
    assert rep.exit_code == 0
    snap = land.read_snapshot(root, "sales")
    assert [f.rule for f in check.check_raw(snap, common.read_json(root / "contracts" / "sales.contract.json"))
            if f.rule == "D-KEY"] == []


def test_check_raw_holds_within_the_known_exceptions(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m", contract=_known_contract(), land_it=False)
    land.land_csv(root, "sales", _defective_csv(tmp_path))
    snap = land.read_snapshot(root, "sales")
    c = common.read_json(root / "contracts" / "sales.contract.json")
    assert [f.rule for f in check.check_raw(snap, c)] == []
    c2 = copy.deepcopy(c)
    c2["expect"].pop("key_repeats")
    for col in c2["columns"]:
        col.pop("known_nulls", None)
    rules = sorted(f.rule for f in check.check_raw(snap, c2))
    assert rules == ["D-KEY", "D-NULL"]


# ----------------------------------------------------------------------------- 1, 3, 4, 5, 6: build and audit

def _built_with_defects(tmp_path: Path, *, fraction: bool = True) -> Path:
    spec = base_spec()
    for s in spec["sheets"]:
        if s.get("type") == "checks":
            for chk in s["checks"]:
                if chk.get("kind") == "equal":
                    chk["format"] = "amount2"
                    break
    root = make_model(tmp_path / "m", contract=_known_contract(), spec=spec, land_it=False)
    rep = land.land_csv(root, "sales", _defective_csv(tmp_path, fraction=fraction))
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    return root


def test_build_writes_at_most_checks_for_known_exceptions(tmp_path: Path) -> None:
    root = _built_with_defects(tmp_path)
    man = manifest.load_manifest(root / "model.xlsx")
    kinds = {c["id"]: c.get("kind") for c in man["checks"]}
    assert kinds["sales_keys"] == "at_most" and kinds["sales_nulls_region"] == "at_most"
    assert kinds["sales_nulls_amount"] == "zero"
    assert "sales_key_repeats" in man["inputs"] and "sales_known_nulls_region" in man["inputs"]
    ws = load(root / "model.xlsx")["checks"]
    rows = {ws.cell(r, 2).value: r for r in range(5, ws.max_row + 1)}
    r = rows["sales_keys"]
    assert ws.cell(r, 5).value.startswith("=inputs!") and ws.cell(r, 9).value == f"=--(D{r}>E{r})"
    assert "agreed as known" in ws.cell(r, 3).value


def test_dup_helper_compares_exactly_with_the_earlier_rows(tmp_path: Path) -> None:
    root = _built_with_defects(tmp_path)
    man = manifest.load_manifest(root / "model.xlsx")
    h = manifest.helper_sheet(man, "sales")
    cols = {c["id"]: c for c in h["columns"]}
    assert cols["dup_key"]["kind"] == "dupkey" and cols["dup"]["kind"] == "dup"
    ws = load(root / "model.xlsx")["h_sales"]
    k, d = cols["dup_key"]["col"], cols["dup"]["col"]
    assert ws[f"{k}2"].value == '=C2&"|"&D2'
    # v2, A-aera-9: EXACT (exact text, case-sensitive, no wildcard); 1 on a later copy only
    assert ws[f"{d}2"].value == f"=--(SUMPRODUCT(--EXACT({k}$2:{k}2,{k}2))>1)*B2"
    assert ws[f"{d}9"].value == f"=--(SUMPRODUCT(--EXACT({k}$2:{k}9,{k}9))>1)*B9"
    assert schemas.validate(man, "manifest") == []
    rep = audit.audit_workbook(root / "model.xlsx", strict=True, record=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]


def test_single_key_uses_the_key_column_and_wildcards_stay_in_the_workbook(tmp_path: Path) -> None:
    c = _known_contract(repeats=0, nulls=0)
    c["key"] = ["invoice_id"]
    header, body = _rows()
    seen, unique = set(), []
    for r in body:
        if r[0] not in seen:
            seen.add(r[0])
            unique.append(r)
    root = make_model(tmp_path / "m", contract=c, land_it=False)
    assert land.land_csv(root, "sales", _write_csv(tmp_path / "u.csv", header, unique)).exit_code == 0
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    h = manifest.helper_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")
    cols = {x["id"]: x for x in h["columns"]}
    assert "dup_key" not in cols and cols["dup"]["formula"] == "=--(SUMPRODUCT(--EXACT(C$2:C2,C2))>1)*B2"
    unique[3][0] = "0001*7"                                    # a wildcard character in a key value
    root2 = make_model(tmp_path / "m2", contract=c, land_it=False)
    assert land.land_csv(root2, "sales", _write_csv(tmp_path / "w.csv", header, unique)).exit_code == 0
    rep = build.build(root2 / "model.spec.json", strict=True)
    # v2: EXACT has no wildcards, so the key check stays in the workbook
    assert rep.exit_code == 0 and not any(f.rule == "I-KEYS-OUTSIDE" for f in rep.findings)
    h2 = manifest.helper_sheet(manifest.load_manifest(root2 / "model.xlsx"), "sales")
    assert "dup" in {x["id"] for x in h2["columns"]}
    # a part of a joined key that holds '|' still moves the key check to rotm.py check
    c3 = _known_contract(repeats=0, nulls=0)
    rows3 = [list(r) for r in unique]
    rows3[3][0] = "0001|7"
    root3 = make_model(tmp_path / "m3", contract=c3, land_it=False)
    assert land.land_csv(root3, "sales", _write_csv(tmp_path / "p.csv", header, rows3)).exit_code == 0
    rep3 = build.build(root3 / "model.spec.json")
    assert any(f.rule == "I-KEYS-OUTSIDE" and "'|'" in f.message for f in rep3.findings)
    h3 = manifest.helper_sheet(manifest.load_manifest(root3 / "model.xlsx"), "sales")
    assert "dup" not in {x["id"] for x in h3["columns"]}


def test_number_column_with_fractions_shows_two_decimals_and_audit_agrees(tmp_path: Path) -> None:
    root = _built_with_defects(tmp_path, fraction=True)
    man = manifest.load_manifest(root / "model.xlsx")
    raw = manifest.raw_sheet(man, "sales")
    fmts = dict(zip(raw["columns"], raw["formats"]))
    assert fmts["amount"] == "amount2" and fmts["line_no"] == "count" and fmts["invoice_id"] == "text"
    wb = load(root / "model.xlsx")
    assert wb["raw_sales"]["F2"].number_format == '#,##0.00_);(#,##0.00);"-"_)'
    rep = audit.audit_workbook(root / "model.xlsx", strict=True, record=False)
    assert not [f for f in rep.findings if f.rule == "S-NUMFMT"]


def test_whole_numbers_keep_the_type_format(tmp_path: Path) -> None:
    root = _built_with_defects(tmp_path, fraction=False)
    raw = manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")
    assert dict(zip(raw["columns"], raw["formats"]))["amount"] == "amount"


def test_more_than_two_decimals_show_general(tmp_path: Path) -> None:
    header, body = _rows()
    body = [list(r) for r in body]
    body[0][5] = "19250.125"
    c = base_contract()
    c["expect"] = {"rows": {"min": 1, "max": 1000}}
    root = make_model(tmp_path / "m", contract=c, land_it=False)
    assert land.land_csv(root, "sales", _write_csv(tmp_path / "d.csv", header, body)).exit_code == 0
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    raw = manifest.raw_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")
    assert dict(zip(raw["columns"], raw["formats"]))["amount"] == "general"


def test_contract_format_is_built_and_audited_alike(tmp_path: Path) -> None:
    c = base_contract()
    for col in c["columns"]:
        if col["name"] == "amount":
            col["format"] = "amount1"
    root = make_model(tmp_path / "m", contract=c)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    assert load(root / "model.xlsx")["raw_sales"]["F2"].number_format == '#,##0.0_);(#,##0.0);"-"_)'
    rep = audit.audit_workbook(root / "model.xlsx", strict=True, record=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    # a raw cell changed afterwards is still found
    import openpyxl
    wb = openpyxl.load_workbook(root / "model.xlsx")
    wb["raw_sales"]["F3"].number_format = "0"
    wb.save(root / "changed.xlsx")
    common.write_json(root / "changed.manifest.json", manifest.load_manifest(root / "model.xlsx")
                      | {"workbook": "changed.xlsx"})
    rep = audit.audit_workbook(root / "changed.xlsx", record=False)
    assert any(f.rule == "S-NUMFMT" and f.cell == "F3" for f in rep.findings)


def test_spec_check_format_reaches_the_value_cells(tmp_path: Path) -> None:
    root = _built_with_defects(tmp_path)
    spec = base_spec()
    first_equal = next(chk for s in spec["sheets"] if s.get("type") == "checks" for chk in s["checks"]
                       if chk.get("kind") == "equal")
    ws = load(root / "model.xlsx")["checks"]
    r = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value == first_equal["id"])
    assert ws.cell(r, 4).number_format == '#,##0.00_);(#,##0.00);"-"_)'
    bad = copy.deepcopy(spec)
    for s in bad["sheets"]:
        if s.get("type") == "checks":
            s["checks"][0]["format"] = "amount_9"
    root2 = make_model(tmp_path / "m2", spec=bad)
    with pytest.raises(common.Refusal) as exc:
        build.build(root2 / "model.spec.json", strict=True)
    assert "amount_9" in "\n".join([exc.value.message] + list(exc.value.details))


# ----------------------------------------------------------------------------- 2. versions

def test_versions_history_changes_and_reused_reply(tmp_path: Path) -> None:
    root = tmp_path / "m"
    sample = _sample_csv(tmp_path)
    contract.propose(root, "sales", csv=sample, key="txn_id", grain="one row per line", rows="151")
    task = root / "task.md"
    task.write_text("record it as agreed by me with the note 'pre-agreed for this test'\n", encoding="utf-8")
    data = contract.agree(root, "sales", reply="pre-agreed for this test", by="Max", channel="task-file",
                          source_file=task)
    assert data["agreement"]["version"] == 1 and "reply_reused_from" not in data["agreement"]
    contract.propose(root, "sales", csv=sample, key="txn_id", grain="one row per line", rows="151",
                     units=["amount=KRW"], force=True)
    data = common.read_json(root / "contracts" / "sales.contract.json")
    assert data["version"] == 2 and data["agreement"]["status"] == "proposed"
    prev = contract.last_agreed(data)
    assert prev["version"] == 1 and prev["contract_sha256"] and prev["agreed_content"]["key"] == ["txn_id"]
    assert contract.changes_between(prev["agreed_content"], data) == ["column amount unit: none -> KRW"]
    md = (root / "contracts" / "sales.contract.md").read_text(encoding="utf-8")
    assert "Changes since version 1, agreed by Max" in md and "column amount unit" in md
    data = contract.agree(root, "sales", reply="pre-agreed for this test", by="Max", channel="task-file",
                          source_file=task)
    ag = data["agreement"]
    assert ag["version"] == 2 and ag["reply_reused_from"] == 1
    md = (root / "contracts" / "sales.contract.md").read_text(encoding="utf-8")
    assert "(contract version 2; the same words as for version 1)" in md and "Agreed by Max" in md
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_a_stale_agreement_keeps_no_content_it_cannot_prove(tmp_path: Path) -> None:
    root = tmp_path / "m"
    path = contract.propose(root, "sales", csv=_sample_csv(tmp_path), key="txn_id", grain="one row per line",
                            rows="151")
    contract.agree(root, "sales", reply="agreed", by="Max")
    data = common.read_json(path)
    data["grain"] = "one row per transaction"           # edited by hand after the agreement: stale
    common.write_json(path, data)
    assert modelfolder.contract_state(root, "sales") == "stale"
    contract.show(root, "sales")              # v3 (C-sol-3, P4): the edited contract is shown before it is agreed
    data = contract.agree(root, "sales", reply="agreed again", by="Max")
    entry = data["agreement"]["history"][-1]
    assert entry["state"] == "stale" and entry["version"] == 1 and "agreed_content" not in entry
    assert contract.last_agreed(data) is None
    assert "Changes since version" not in (root / "contracts" / "sales.contract.md").read_text(encoding="utf-8")


def test_cli_agree_prints_the_changes_and_the_reuse(tmp_path: Path) -> None:
    root = tmp_path / "m"
    sample = _sample_csv(tmp_path)
    contract.propose(root, "sales", csv=sample, key="txn_id", grain="one row per line", rows="151")
    contract.agree(root, "sales", reply="agreed", by="Max")
    contract.propose(root, "sales", csv=sample, key="txn_id", grain="one row per line", rows="151",
                     units=["amount=KRW"], force=True)
    r = subprocess.run(ROTM + ["contract", "agree", str(root), "--table", "sales", "--reply", "agreed", "--by",
                               "Max"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "version 2 agreed by Max" in r.stdout and "changes since version 1" in r.stdout
    assert "same words that were recorded for version 1" in r.stdout
    r = subprocess.run(ROTM + ["contract", "status", str(root)], capture_output=True, text=True, encoding="utf-8")
    assert "agreed  version 2" in r.stdout


def test_a_shipped_contract_without_version_still_reads_as_agreed() -> None:
    example = SCRIPTS.parent / "examples" / "sales_vs_target"
    assert modelfolder.contract_state(example, "sales") == "agreed"
    data = common.read_json(example / "contracts" / "sales.contract.json")
    assert "version" not in data
    assert contract.render_md(data) == (example / "contracts" / "sales.contract.md").read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- 7, 8: catalogue and SQL

def test_datevalue_and_timevalue_are_in_the_catalogue() -> None:
    assert functions.classify("DATEVALUE") == "allowed" and functions.classify("TIMEVALUE") == "allowed"
    from lib import lift
    assert {"DATEVALUE", "TIMEVALUE"} <= lift.supported_functions()
    assert lift.DATEVALUE("2026-01-31") == 46053 and lift.TIMEVALUE("06:00") == 0.25


@pytest.mark.parametrize("expr, ok", [
    ("CAST(i.invoice_id AS STRING)", True), ("ROW_NUMBER() OVER ()", True), ("o.amount - o.discount", True),
    ("CASE WHEN x > 0 THEN 'a' ELSE 'b' END", True), ("DATE '2026-01-01'", True), ("`my col`", True),
    ("line number of the record in t.csv, header excluded", False), ("line number", False), ("x AS y", False),
    ("SUM(x", False), ("'abc", False)])
def test_sql_expression_problem(expr: str, ok: bool) -> None:
    assert (land.sql_expression_problem(expr) is None) is ok


def test_land_writes_null_for_prose_and_warns(tmp_path: Path) -> None:
    c = base_contract()
    c["columns"][1]["source_expression"] = "the line number, counted from 1"
    root = make_model(tmp_path / "m", contract=c, land_it=False)
    rep = common.Report("land", root.as_posix())
    land.write_queries(root, "sales", report=rep)
    sql = (root / "queries" / "sales.sql").read_text(encoding="utf-8")
    assert "  NULL AS line_no," in sql and "replace NULL" in sql
    assert [f.rule for f in rep.findings] == ["L-SQL-EXPR"]

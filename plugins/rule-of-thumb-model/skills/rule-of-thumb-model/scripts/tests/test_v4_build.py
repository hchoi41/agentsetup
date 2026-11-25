"""Tests of the v4 build changes (package P2 of the v4 plan). Owner: P2.

Entries: [C-sol-1] (a query attached by hand is carried over a rebuild, refused without Excel, or dropped with
--drop-hand-queries), [C-sol-9] (the marker's workbook follows the spec's output), [C-sol-12, C-sol-13, J-1] (the
worked example's var_pct check, numeric note ordinals, ALERT in an alert row's Result cell) and supporting
[C-sol-3] (an incomplete expression is refused with its place in the expression). Tests marked excel drive Excel
through lib.excel. Integration tests (plan section 7): I-2 needs P5's C-ALERT in audit; I-4 runs check, which P3
changes.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from lib import build, check, common, land, manifest, modelfolder
from test_build import base_spec, make_model, sales_rows

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
EXAMPLE = SKILL / "examples" / "sales_vs_target"


def rotm(*args: object, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def out(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def tree_hashes(folder: Path) -> dict[str, str | None]:
    if not folder.is_dir():
        return {}
    return {p.relative_to(folder).as_posix(): sha(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def write_rows(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def raw_entry(wb: Path) -> dict:
    return manifest.raw_sheet(manifest.load_manifest(wb), "sales")


def mark_hand_attached(wb: Path, query_name: str = "q_finance") -> None:
    """The manifest as check --accept-refresh leaves it after accepting a query attached by hand."""
    data = manifest.load_manifest(wb)
    e = manifest.raw_sheet(data, "sales")
    e.update(landing="power_query", query_attach="hand", query_name=query_name)
    e.pop("query_contract_sha256", None)
    manifest.write_manifest(wb, data)


# ============================================================================= [C-sol-1]

_QT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/queryTable"


def _make_query_table(wb: Path, display_name: str, query: str) -> None:
    """(copied from test_v3_raw.py, so this file does not depend on another package's tests) Turn the table `display_name` of a saved workbook into a query table of connection 1 (the parts Excel writes
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



@pytest.mark.excel
def test_inventory_returns_the_m_text_of_each_query(tmp_path: Path) -> None:
    from lib import excel
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    c = common.read_json(root / "contracts" / "sales.contract.json")
    m = land.m_text(c, csv_path=root / "raw" / "sales.csv")
    res = excel.pq_attach(wb, sheet="raw_sales", table_name="tbl_finance", query_name="q finance", m_text=m)
    assert res.ok, res.message()
    inv = excel.inventory(wb)
    formulas = {q["name"]: q["formula"] for q in inv["query_formulas"]}
    assert set(formulas) == {"q finance"}
    # measured 2026-11-25 (Excel 16): Excel gives the M text back without its trailing line break
    assert formulas["q finance"].replace("\r\n", "\n").rstrip() == m.replace("\r\n", "\n").rstrip()
    tables = next(s for s in inv["sheets"] if s["name"] == "raw_sales")["tables"]
    assert [t["name"] for t in tables if t["query"]] == ["tbl_finance"]


@pytest.mark.excel
def test_hand_attached_query_is_carried_over_a_rebuild(tmp_path: Path) -> None:
    """I-4: build a static model; attach a query over a second CSV by hand (lib.excel, not rotm.py); accept it;
    rebuild with --strict --force; change the second CSV; check --refresh: the raw sheet and one output cell hold
    the changed CSV's values and the manifest still records the hand attach."""
    import openpyxl
    from lib import excel
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    rows = sales_rows()
    for r in rows:
        r["amount"] = f"{float(r['amount']) + 1:.2f}"
    second = write_rows(tmp_path / "finance" / "sales.csv", rows)
    c = common.read_json(root / "contracts" / "sales.contract.json")
    m = land.m_text(c, csv_path=second)
    res = excel.pq_attach(wb, sheet="raw_sales", table_name="tbl_finance", query_name="q_finance", m_text=m)
    assert res.ok, res.message()
    total = sum(float(r["amount"]) for r in rows)
    acc = rotm("check", root, "--accept-refresh", "--control", f"sales.amount={total:.2f}")
    assert acc.returncode == 0, out(acc)
    e = raw_entry(wb)
    assert e["query_attach"] == "hand" and e["query_name"] == "q_finance", e

    reb = rotm("build", root, "--strict", "--force")
    assert reb.returncode == 0, out(reb)
    text = out(reb)
    assert "keeps its Power Query table" not in text
    assert ("raw_sales: the query q_finance attached by hand was carried over: its M text is kept in "
            "queries/sales.hand.pq.m and it was attached again with the same name") in text
    kept = root / "queries" / "sales.hand.pq.m"
    assert kept.is_file() and kept.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() == \
        m.replace("\r\n", "\n").rstrip()
    assert check.query_tables(wb) == {"raw_sales": "q_finance"}
    e = raw_entry(wb)
    assert e["landing"] == "power_query" and e["query_attach"] == "hand" and e["query_name"] == "q_finance"
    assert e["query_file"] == "queries/sales.hand.pq.m" and "query_contract_sha256" not in e
    assert e["workbook_digest"] == e["digest"]

    rows[0]["amount"] = f"{float(rows[0]['amount']) + 1000:.2f}"
    write_rows(second, rows)
    ref = rotm("check", root, "--refresh")
    assert "refreshed raw_sales" in out(ref), out(ref)
    book = openpyxl.load_workbook(wb, data_only=True)
    assert abs(float(book["raw_sales"]["F2"].value) - float(rows[0]["amount"])) < 1e-9
    # the block 'actual' sums the helper amount_k (amount / the input scale, 1000); its total is calc!J10
    total_cell = manifest.sheet_entry(manifest.load_manifest(wb), "calc")["blocks"][0]["total"]
    scale = float(book["inputs"][manifest.load_manifest(wb)["inputs"]["scale"].split("!")[1]].value)
    assert abs(float(book["calc"][total_cell].value) - sum(float(r["amount"]) for r in rows) / scale) < 1e-6
    e = raw_entry(wb)
    assert e["query_attach"] == "hand" and e["query_name"] == "q_finance"
    assert e["query_file"] == "queries/sales.hand.pq.m"


def test_rebuild_over_a_hand_attached_query_without_excel_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    # (a) the manifest records query_attach hand
    root = make_model(tmp_path / "a")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    mark_hand_attached(wb)
    files = [wb, manifest.manifest_path(wb), root / "raw" / "sales.csv", root / "reports" / "status.json"]
    before = {p.name: sha(p) for p in files}
    queries = tree_hashes(root / "queries")
    proc = rotm("build", root, "--strict", "--force")
    assert proc.returncode == 2, out(proc)
    text = out(proc)
    assert "raw_sales is loaded by the query q_finance attached by hand, and Excel is not available" in text
    assert "nothing was written" in text and "--drop-hand-queries" in text
    assert {p.name: sha(p) for p in files} == before
    assert tree_hashes(root / "queries") == queries
    assert not list(root.glob("*.bak_*")) and not list(root.glob("*.tmp*"))
    # (b) the workbook holds a query table at A1 of raw_sales that no rotm.py command attached
    root = make_model(tmp_path / "b")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    _make_query_table(wb, "tbl_raw_sales", "q_by_hand")
    before = {p.name: sha(p) for p in [wb, manifest.manifest_path(wb), root / "raw" / "sales.csv"]}
    proc = rotm("build", root, "--strict", "--force")
    assert proc.returncode == 2, out(proc)
    assert "raw_sales is loaded by the query q_by_hand attached by hand" in out(proc)
    assert {p.name: sha(p) for p in [wb, manifest.manifest_path(wb), root / "raw" / "sales.csv"]} == before


def test_rebuild_keeps_the_generated_query_rule_for_a_query_rotm_attached(tmp_path: Path, monkeypatch) -> None:
    """A query land --power-query attached (contract hash, query_name q_<t>, no query_attach) is not hand-attached:
    without Excel build warns W-PQ-NOT-ATTACHED as before and does not refuse."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    _make_query_table(wb, "tbl_raw_sales", "q_sales")
    data = manifest.load_manifest(wb)
    e = manifest.raw_sheet(data, "sales")
    c = common.read_json(root / "contracts" / "sales.contract.json")
    e.update(landing="power_query", query_name="q_sales", query_contract_sha256=c["agreement"]["contract_sha256"])
    manifest.write_manifest(wb, data)
    rep = build.build(root / "model.spec.json", force=True)
    assert [f.rule for f in rep.findings] == ["W-PQ-NOT-ATTACHED"], [f.line() for f in rep.findings]
    assert not any("keeps its Power Query table" in n for n in rep.notes)


def test_drop_hand_queries_rebuilds_a_static_table(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    mark_hand_attached(wb)
    proc = rotm("build", root, "--force", "--drop-hand-queries")
    assert proc.returncode == 0, out(proc)
    text = out(proc)
    assert "W-PQ-NOT-ATTACHED" in text
    assert ("raw_sales holds static values: --drop-hand-queries removed the query q_finance attached by hand; "
            "attach it by hand again") in text
    assert "Attaching a query by hand" in text and "--accept-refresh" in text
    e = raw_entry(wb)
    assert e["landing"] == "static" and not [k for k in e if k.startswith("query_")], e
    assert check.query_tables(wb) == {}
    ref = rotm("check", root, "--refresh")
    assert "X-NO-QUERY" in out(ref), out(ref)


# ============================================================================= [C-sol-9]

def test_custom_output_name_is_found_by_the_folder_commands(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    spec = base_spec()
    spec["output"] = "forecast.xlsx"
    root = make_model(tmp_path / "m", spec=spec)
    assert modelfolder.read_marker(root)["workbook"] == "model.xlsx"
    proc = rotm("build", root, "--strict")
    assert proc.returncode == 0, out(proc)
    assert (root / "forecast.xlsx").is_file() and not (root / "model.xlsx").exists()
    assert modelfolder.read_marker(root)["workbook"] == "forecast.xlsx"
    assert f'check "{root.as_posix()}"' in out(proc)            # the folder now resolves to forecast.xlsx
    aud = rotm("audit", root, "--strict", "--json", "-")
    assert aud.returncode == 0, out(aud)
    assert Path(json.loads(aud.stdout)["target"]).name == "forecast.xlsx"
    chk = rotm("check", root, "--no-excel", "--json", "-")
    assert chk.returncode == 3, out(chk)
    assert Path(json.loads(chk.stdout)["target"]).name == "forecast.xlsx" and "not recalculated" in out(chk)
    st = rotm("status", root)
    assert st.returncode == 0 and "forecast.xlsx" in out(st), out(st)
    other = rotm("build", root, "-o", root / "other.xlsx")
    assert other.returncode == 0, out(other)
    assert modelfolder.read_marker(root)["workbook"] == "forecast.xlsx"
    assert f'check "{(root / "other.xlsx").as_posix()}"' in out(other)


# ============================================================================= [C-sol-12, C-sol-13, J-1]

def _example_copy(tmp_path: Path, monkeypatch) -> Path:
    """A copy of the worked example without its built files; its 'example' agreements count under tmp_path."""
    from lib import spec as specmod
    monkeypatch.setenv(specmod.EXAMPLE_ROOTS_ENV, str(tmp_path))
    dest = tmp_path / "sales_vs_target"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("*.xlsx", "*.manifest.json", "reports", "lift",
                                                                 "__pycache__", "*.pyc"))
    return dest


def _set_item(spec: dict, block_id: str, item_id: str, expr: str) -> None:
    for s in spec["sheets"]:
        for b in s.get("blocks", []):
            if b["id"] == block_id:
                next(i for i in b["items"] if i["id"] == item_id)["expr"] = expr
                return
    raise KeyError(block_id)


@pytest.mark.excel
def test_example_var_pct_check_fails_when_the_item_is_zero(tmp_path: Path, monkeypatch) -> None:
    root = _example_copy(tmp_path, monkeypatch)
    spec = common.read_json(root / "model.spec.json")
    assert "var_pct_all" in [c["id"] for s in spec["sheets"] if s["type"] == "checks" for c in s["checks"]]
    proc = rotm("build", root, "--strict", "--force")
    assert proc.returncode == 0, out(proc)
    chk = rotm("check", root, "--strict")
    assert chk.returncode == 0, out(chk)
    assert "checks: 24 of 24 hold" in out(chk), out(chk)
    bad = tmp_path / "bad"
    shutil.copytree(root, bad, ignore=shutil.ignore_patterns("*.xlsx", "*.manifest.json", "reports"))
    spec = common.read_json(bad / "model.spec.json")
    _set_item(spec, "bridge", "var_pct", "0")
    common.write_json(bad / "model.spec.json", spec)
    proc = rotm("build", bad, "--strict", "--force")
    assert proc.returncode == 0, out(proc)
    chk = rotm("check", bad, "--strict", "--json", "-")
    assert chk.returncode == 1, out(chk)
    failed = [f for f in json.loads(chk.stdout)["findings"] if f["rule"] == "C-FAIL"]
    named = [f["data"].get("check") for f in failed]
    assert "var_pct_all" in named and "master" in named, failed
    assert [n for n in named if n not in ("var_pct_all", "master")] == [], failed


def test_example_var_pct_check_is_not_a_self_check(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _example_copy(tmp_path, monkeypatch)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    data = manifest.load_manifest(root / "model.xlsx")
    errors = [c for c in data["checks"] if c.get("severity", "error") == "error"]
    assert "var_pct_all" in [c["id"] for c in errors] and len(errors) == 24
    entry = next(c for c in data["checks"] if c["id"] == "var_pct_all")
    bridge = next(b for s in data["sheets"] for b in s.get("blocks") or [] if b["id"] == "bridge")
    assert entry.get("targets") == [f"calc!{bridge['body']}"], entry
    assert not [f for f in rep.findings if f.rule == "W-SELF-CHECK"]


def test_note_ordinals_are_numbers(tmp_path: Path, monkeypatch) -> None:
    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    spec = base_spec()
    notes = next(s for s in spec["sheets"] if s["type"] == "notes")
    notes["notes"] = notes["notes"] + [{"text": "Targets come from the finance plan.", "source": "inputs"},
                                       {"text": "Months end on the last calendar day.", "source": "spec"}]
    root = make_model(tmp_path / "m", spec=spec)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    ws = openpyxl.load_workbook(root / "model.xlsx")["notes"]
    cells = [ws.cell(row=r, column=2) for r in range(6, 9)]
    assert [c.value for c in cells] == [1, 2, 3]
    assert [c.data_type for c in cells] == ["n", "n", "n"]
    assert all(c.number_format == "General" for c in cells)
    types = manifest.cell_types(manifest.load_manifest(root / "model.xlsx"), "notes")
    assert all(types.get(("notes", c.coordinate)) == "row_header" for c in cells), types
    aud = rotm("audit", root, "--strict")
    assert aud.returncode == 0, out(aud)


def _alert_model(tmp_path: Path, *, failing: bool) -> Path:
    spec = base_spec()
    checks = next(s for s in spec["sheets"] if s["type"] == "checks")["checks"]
    alert = next(c for c in checks if c["id"] == "neg_months")
    alert["actual"] = "COUNTIF({blk:bridge},\">-1000000000\")"          # every bridge cell: the alert is raised
    if failing:
        tgt = next(c for c in checks if c["id"] == "tgt_ties_input")
        tgt["expected"] = "SUM({in:target})+1"                         # the error check fails
    return make_model(tmp_path, spec=spec)


def _ref(ref: str) -> tuple[str, str]:
    sheet, cell = ref.replace("$", "").split("!")
    return sheet, cell


def test_alert_result_cell_has_the_alert_format(tmp_path: Path, monkeypatch) -> None:
    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _alert_model(tmp_path / "m", failing=False)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    data = manifest.load_manifest(root / "model.xlsx")
    book = openpyxl.load_workbook(root / "model.xlsx")
    by_id = {c["id"]: c for c in data["checks"]}
    sheet, cell = _ref(by_id["neg_months"]["result"])
    assert book[sheet][cell].number_format == '[Red]"ALERT";[Red]"ALERT";"OK"'
    assert manifest.cell_types(data, sheet).get((sheet, cell)) == "alert"
    sheet, cell = _ref(by_id["tgt_ties_input"]["result"])
    assert book[sheet][cell].number_format == '[Red]"FAIL";[Red]"FAIL";"OK"'
    assert manifest.cell_types(data, sheet).get((sheet, cell)) == "check"


@pytest.mark.excel
def test_raised_alert_shows_alert_and_strict_audit_reports_c_alert(tmp_path: Path) -> None:
    """I-2: the alert format and ALERT shown; a failing error check keeps the check format and the master fails;
    audit --strict exits 0 with one C-ALERT (the C-ALERT assertion needs P5's change in audit.py)."""
    import openpyxl
    root = _alert_model(tmp_path / "a", failing=False)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    chk = rotm("check", root, "--strict")
    assert chk.returncode == 0, out(chk)
    data = manifest.load_manifest(root / "model.xlsx")
    by_id = {c["id"]: c for c in data["checks"]}
    sheet, cell = _ref(by_id["neg_months"]["result"])
    assert openpyxl.load_workbook(root / "model.xlsx", data_only=True)[sheet][cell].value == 1
    assert openpyxl.load_workbook(root / "model.xlsx")[sheet][cell].number_format == \
        '[Red]"ALERT";[Red]"ALERT";"OK"'                                 # 1 shows ALERT in red
    bad = _alert_model(tmp_path / "b", failing=True)
    assert build.build(bad / "model.spec.json").exit_code in (0, 1)
    chk = rotm("check", bad, "--strict")
    assert chk.returncode == 1, out(chk)
    data = manifest.load_manifest(bad / "model.xlsx")
    by_id = {c["id"]: c for c in data["checks"]}
    values = openpyxl.load_workbook(bad / "model.xlsx", data_only=True)
    looks = openpyxl.load_workbook(bad / "model.xlsx")
    sheet, cell = _ref(by_id["tgt_ties_input"]["result"])
    assert values[sheet][cell].value == 1 and looks[sheet][cell].number_format == '[Red]"FAIL";[Red]"FAIL";"OK"'
    msheet, mcell = _ref(data["master_check"])
    assert values[msheet][mcell].value == 1                              # the master check fails
    aud = rotm("audit", root, "--strict", "--json", "-")
    rules = [f["rule"] for f in json.loads(aud.stdout)["findings"]]
    assert "C-FAIL" not in rules, rules                                  # needs P5 (audit.py C-ALERT)
    assert rules.count("C-ALERT") == 1 and aud.returncode == 0, out(aud)


# ============================================================================= supporting [C-sol-3]

def test_f_parse_names_the_place_in_the_expression(tmp_path: Path, monkeypatch) -> None:
    """An F-PARSE issue with start and end (P1's data) is mapped to the expression, as F-DEPTH is. The issue is
    given by a stand-in for formula.check_formula, so the test does not depend on P1's completeness test."""
    from lib import formula
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    spec = base_spec()
    expr = next(i for s in spec["sheets"] for b in s.get("blocks", []) if b["id"] == "bridge"
                for i in b["items"] if i["id"] == "var")["expr"]
    assert expr == "{r:act}-{r:tg}"
    root = make_model(tmp_path / "m", spec=spec)
    real = formula.check_formula
    seen: list[str] = []

    def fake(f: str, *args, **kwargs):
        issues = list(real(f, *args, **kwargs))
        body = f[1:]
        # the bridge item var resolves to D$<r>-D$<r'> (two references of this column, one '-')
        if body.count("-") == 1 and body.count("$") == 2 and "!" not in body and "(" not in body:
            k = f.index("-")
            seen.append(f)
            issues.append(formula.Issue("F-PARSE", "error", f"the formula is not complete: the operator '-' has no "
                                        f"operand after it at character {k} ({f})", "complete the expression",
                                        {"start": k, "end": k + 1, "reason": "the operator '-' has no operand after it"}))
        return issues

    monkeypatch.setattr(formula, "check_formula", fake)
    with pytest.raises(common.Refusal) as exc:
        build.build(root / "model.spec.json", strict=True)
    assert seen, "the stand-in did not see the item's formula"
    text = "\n".join([exc.value.message] + list(exc.value.details))
    assert "complete the expression at '-' (characters 8 to 8 of the expression): an operand after each operator, "\
        "every argument the function needs, closed brackets and quotes" in text, text
    assert not (root / "model.xlsx").exists()

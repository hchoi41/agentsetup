"""v2 tests of landing, query stamps, templates and the worked example's specification (package P3).

- [A-aera-2, C-sol-2]: queries/<t>.sql and .pq.m carry the agreed contract's hash and their body's hash; land writes
  an unedited query of an older contract again, refuses a hand-edited one, and offers --rewrite-queries and
  --keep-queries; the Power Query attach refuses a stale query before Excel starts and records the contract hash;
  init --template copies neither queries/ nor raw/.
- [A-aera-14, C-sol-11]: the worked example ties the variance % grid to actual and target (Excel).
- [C-sol-6]: the SQL inside a database Power Query quotes names for its connector.
- Supporting: exact control totals (A-aera-4), accepted control totals (A-aera-7), the 32,767-character text limit
  (A-aera-10), the placeholder <model folder> in queries/<t>.pq.m (O-1), the status line of the check step (J-4 d).
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
DATA = Path(__file__).resolve().parent / "data" / "build"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import common, land, manifest, modelfolder  # noqa: E402

HEADER = ["invoice_id", "line_no", "inv_date", "region", "product", "amount"]
NEW_FROM = "proj.ds.current_lines l JOIN proj.ds.invoices i USING (invoice_id)"


def contract(**changes) -> dict:
    c = json.loads((DATA / "sales.contract.json").read_text(encoding="utf-8"))
    c.update(changes)
    return c


def agree(c: dict) -> dict:
    c = {k: v for k, v in c.items() if k != "agreement"}
    c["agreement"] = {"status": "agreed", "contract_sha256": modelfolder.contract_hash(c), "reply": "ok, agreed",
                      "by": "tester", "at": "2026-11-25T08:00:00+09:00", "channel": "session", "source_file": None,
                      "recorded_by": "test"}
    return c


def reagree(root: Path, table: str = "sales", **source: str) -> str:
    """The contract proposed again with other source fields and agreed again; returns the new contract hash."""
    path = root / "contracts" / f"{table}.contract.json"
    c = common.read_json(path)
    c["source"] = dict(c.get("source") or {}, **source)
    c = agree(c)
    common.write_json(path, c)
    return c["agreement"]["contract_sha256"]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "m"
    modelfolder.init_model(r, name="v2_landing")
    common.write_json(r / "contracts" / "sales.contract.json", agree(contract()))
    return r


def rows() -> list[list[str]]:
    with open(DATA / "sales.csv", encoding="utf-8", newline="") as f:
        return list(csv.reader(f))[1:]


def write_csv(path: Path, body: list[list[str]], header: list[str] = HEADER) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(body)
    return path


def cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=300, env=e)


def text_of(root: Path, name: str) -> str:
    return (root / "queries" / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- A-aera-2, C-sol-2: query stamps

def test_generated_queries_carry_the_agreed_contracts_stamp(root: Path) -> None:
    land.write_queries(root, "sales")
    agreed = common.read_json(root / "contracts" / "sales.contract.json")["agreement"]["contract_sha256"]
    for name, comment in (("sales.sql", "--"), ("sales.pq.m", "//")):
        text = text_of(root, name)
        assert text.startswith(f"{comment} rotm-stamp: contract sha256:{agreed}; body sha256:"), name
        stamp = common.read_query_stamp(text)
        assert stamp["contract_sha256"] == agreed and stamp["edited"] is False, name


def test_a_new_agreement_reaches_unedited_queries(root: Path) -> None:
    land.land_csv(root, "sales", DATA / "sales.csv")
    assert "FROM proj.ds.invoice_lines l JOIN" in text_of(root, "sales.sql")
    new = reagree(root, **{"from": NEW_FROM})
    rep = land.land_csv(root, "sales", DATA / "sales.csv")          # the same rows: the snapshot stays
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    sql = text_of(root, "sales.sql")
    assert f"FROM {NEW_FROM}" in sql and "invoice_lines l" not in sql
    assert common.read_query_stamp(sql)["contract_sha256"] == new
    assert any(n.startswith("queries/sales.sql was written for another contract") and "written again" in n
               for n in rep.notes), rep.notes
    assert common.read_query_stamp(text_of(root, "sales.pq.m"))["contract_sha256"] == new
    # the same through the command without --csv
    reagree(root, where="i.posted AND i.inv_date >= '2026-02-01'")
    proc = cli("land", str(root), "--table", "sales")
    assert proc.returncode == 0, proc.stderr
    assert "written again from the agreed contract" in proc.stdout
    assert "WHERE i.posted AND i.inv_date >= '2026-02-01'" in text_of(root, "sales.sql")


def _edit_sql(root: Path) -> str:
    path = root / "queries" / "sales.sql"
    edited = path.read_text(encoding="utf-8").replace("ORDER BY invoice_id, line_no",
                                                      "-- reviewed by hand\nORDER BY invoice_id, line_no")
    path.write_text(edited, encoding="utf-8")
    return edited


def test_a_hand_edited_query_for_an_older_contract_is_refused(root: Path, tmp_path: Path) -> None:
    land.land_csv(root, "sales", DATA / "sales.csv")
    edited = _edit_sql(root)
    snapshot = (root / "raw" / "sales.csv").read_bytes()
    reagree(root, **{"from": NEW_FROM})
    body = rows()[:-1]
    c = common.read_json(root / "contracts" / "sales.contract.json")
    c["expect"]["totals"][0]["value"] = sum(float(r[5]) for r in body)
    common.write_json(root / "contracts" / "sales.contract.json", agree(c))
    other = write_csv(tmp_path / "other.csv", body)
    with pytest.raises(common.Refusal) as ei:
        land.land_csv(root, "sales", other, force=True)
    assert "queries/sales.sql was edited by hand and its stamp names another contract" in ei.value.message
    assert "nothing was written" in ei.value.message and "queries/sales.pq.m" not in ei.value.message
    assert "--rewrite-queries" in ei.value.fix and "--keep-queries" in ei.value.fix
    assert text_of(root, "sales.sql") == edited
    assert (root / "raw" / "sales.csv").read_bytes() == snapshot, "the snapshot is not replaced"
    proc = cli("land", str(root), "--table", "sales")
    assert proc.returncode == 2 and "queries/sales.sql was edited by hand" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_rewrite_queries_writes_again_and_keeps_the_old_file(root: Path) -> None:
    land.write_queries(root, "sales")
    edited = _edit_sql(root)
    new = reagree(root, **{"from": NEW_FROM})
    proc = cli("land", str(root), "--table", "sales", "--rewrite-queries")
    assert proc.returncode == 0, proc.stderr
    sql = text_of(root, "sales.sql")
    assert f"FROM {NEW_FROM}" in sql and "reviewed by hand" not in sql
    stamp = common.read_query_stamp(sql)
    assert stamp["contract_sha256"] == new and stamp["edited"] is False
    baks = list((root / "queries").glob("sales.sql.bak_*"))
    assert len(baks) == 1 and baks[0].read_text(encoding="utf-8") == edited
    assert "kept the old queries/sales.sql as queries/sales.sql.bak_" in proc.stdout


def test_keep_queries_stamps_a_file_reconciled_by_hand(root: Path) -> None:
    land.write_queries(root, "sales")
    _edit_sql(root)
    new = reagree(root, **{"from": NEW_FROM})
    path = root / "queries" / "sales.sql"
    reconciled = path.read_text(encoding="utf-8").replace("FROM proj.ds.invoice_lines l JOIN", "FROM proj.ds.current_lines l JOIN")
    path.write_text(reconciled, encoding="utf-8")
    with pytest.raises(common.Refusal):
        land.write_queries(root, "sales")
    rep = common.Report("land", root.as_posix())
    land.write_queries(root, "sales", keep=True, report=rep)
    sql = text_of(root, "sales.sql")
    assert "reviewed by hand" in sql and "FROM proj.ds.current_lines l JOIN" in sql
    stamp = common.read_query_stamp(sql)
    assert stamp["contract_sha256"] == new and stamp["edited"] is False
    assert sql.split("\n", 1)[1] == reconciled.split("\n", 1)[1], "the body is kept as edited"
    assert any("--keep-queries" in n for n in rep.notes)
    assert land.write_queries(root, "sales") == []                # current now: kept, no refusal
    with pytest.raises(common.Refusal, match="both given"):
        land.write_queries(root, "sales", overwrite=True, keep=True)


# ----------------------------------------------------------------------------- the Power Query attach

class _FakeJob:
    ok = True
    pid = 0

    def step(self, _name: str) -> dict:
        return {}

    def message(self) -> str:
        return ""


def _built(root: Path) -> Path:
    from lib import build
    land.land_csv(root, "sales", DATA / "sales.csv")
    shutil.copy(DATA / "model.spec.json", root / "model.spec.json")
    rep = build.build(root / "model.spec.json")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    return root / "model.xlsx"


def test_attach_refuses_a_stale_query_before_excel_starts(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import build, excel
    wb = _built(root)
    reagree(root, **{"from": NEW_FROM})

    def no_excel(*_a, **_k):
        raise AssertionError("Excel was asked for")
    monkeypatch.setattr(excel, "excel_status", no_excel)
    monkeypatch.setattr(excel, "pq_attach", no_excel)
    with pytest.raises(common.Refusal) as ei:
        land.attach_power_query(wb, root, "sales")
    assert "queries/sales.pq.m was written for another contract" in ei.value.message
    assert "not attached" in ei.value.message and "--power-query" in ei.value.fix
    (root / "queries" / "sales.pq.m").write_text("let\n    Source = 1\nin\n    Source\n", encoding="utf-8")
    with pytest.raises(common.Refusal, match="has no rotm-stamp line"):
        land.attach_power_query(wb, root, "sales")
    # build's attach reports the refusal as W-PQ-NOT-ATTACHED
    spec = common.read_json(root / "model.spec.json")
    for s in spec["sheets"]:
        if s["type"] == "raw":
            s["landing"] = "power_query"
    common.write_json(root / "model.spec.json", spec)
    monkeypatch.setattr(excel, "excel_status", lambda *a, **k: excel.ExcelStatus(True, "test"))
    rep = build.build(root / "model.spec.json", force=True)
    pq = [f for f in rep.findings if f.rule == "W-PQ-NOT-ATTACHED"]
    assert pq and "has no rotm-stamp line" in pq[0].message


def test_attach_fills_in_the_model_folder_and_records_the_contract_hash(root: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import excel
    wb = _built(root)
    sent: dict = {}

    def fake_attach(workbook, **kw):
        sent.update(kw)
        return _FakeJob()
    monkeypatch.setattr(excel, "excel_status", lambda *a, **k: excel.ExcelStatus(True, "test"))
    monkeypatch.setattr(excel, "pq_attach", fake_attach)
    rep = land.attach_power_query(wb, root, "sales")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    on_disk = text_of(root, "sales.pq.m")
    assert 'File.Contents("<model folder>\\raw\\sales.csv")' in on_disk
    assert str(root) not in on_disk and root.as_posix() not in on_disk, "the file holds no path of this computer"
    assert f'File.Contents("{root / "raw" / "sales.csv"}")' in sent["m_text"]
    assert "<model folder>" not in sent["m_text"]
    entry = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    agreed = common.read_json(root / "contracts" / "sales.contract.json")["agreement"]["contract_sha256"]
    assert entry["landing"] == "power_query" and entry["query_contract_sha256"] == agreed


@pytest.mark.excel
def test_a_stamped_query_with_the_placeholder_attaches_in_excel(root: Path) -> None:
    from lib import excel
    wb = _built(root)
    rep = land.attach_power_query(wb, root, "sales")
    assert rep.exit_code == 0 and rep.data["rows"] == 72, [f.line() for f in rep.findings]
    assert not excel.pid_alive(rep.data["pid"])
    entry = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    assert manifest.sheet_digest(wb, "raw_sales", entry["types"])[0] == entry["digest"]


# ----------------------------------------------------------------------------- init --template

def _templates() -> list[str]:
    names = ["starter"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        names += ["ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast"]
    return names + [str(SKILL / "examples" / "sales_vs_target")]


@pytest.mark.parametrize("template", _templates(), ids=lambda t: Path(t).name)
def test_a_model_made_from_a_template_has_no_queries_and_no_snapshot(tmp_path: Path, template: str) -> None:
    root = tmp_path / "new_model"
    marker = modelfolder.init_model(root, template=template)
    assert marker["reset_tables"]
    assert not list((root / "queries").glob("*.sql")) and not list((root / "queries").glob("*.m"))
    assert not list((root / "raw").glob("*.csv")) and not list((root / "raw").glob("*.controls.json"))
    assert not (root / "model.xlsx").exists() and not (root / "model.manifest.json").exists()
    assert not any((root / "reports").iterdir())
    for t in marker["reset_tables"]:
        assert modelfolder.contract_state(root, t) == "proposed"
        assert (root / "data" / f"{t}.csv").is_file(), f"the sample extract data/{t}.csv stays"


def test_after_agreement_land_writes_the_templates_queries_and_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "from_starter"
    modelfolder.init_model(root, template="starter")
    proc = cli("init", str(tmp_path / "printed"), "--template", "starter")
    assert proc.returncode == 0 and "land" in proc.stdout and "data/lines.csv" in proc.stdout
    path = root / "contracts" / "lines.contract.json"
    common.write_json(path, agree(common.read_json(path)))
    proc = cli("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"))
    assert proc.returncode == 0, proc.stderr
    assert (root / "raw" / "lines.csv").is_file()
    agreed = common.read_json(path)["agreement"]["contract_sha256"]
    for name in ("lines.sql", "lines.pq.m"):
        assert common.read_query_stamp(text_of(root, name))["contract_sha256"] == agreed
    assert "<model folder>\\raw\\lines.csv" in text_of(root, "lines.pq.m")


# ----------------------------------------------------------------------------- C-sol-6: connector quoting

def _reserved(system: str) -> dict:
    c = contract()
    c["source"]["system"] = system
    c["columns"][0]["name"] = "order"
    c["columns"][0]["source_expression"] = "[order]" if system == "SQL Server" else "invoice_id"
    c["key"] = ["order", "line_no"]
    return agree(c)


def test_database_query_quotes_names_for_sql_server(root: Path) -> None:
    c = _reserved("SQL Server")                                  # the reviewers' case: source [order], name order
    m = land.m_text(c)
    # v4, A-aera-11: a quoted source identifier shows on both sides of AS
    assert "Sql.Database(" in m and "SELECT#(lf)  [order] AS [order],#(lf)" in m and "l.line_no AS [line_no]" in m
    assert "ORDER BY [order], [line_no]" in m and "`" not in m
    sql = land.sql_text(c)                                       # queries/<t>.sql stays GoogleSQL
    assert "[order] AS `order`" in sql and "ORDER BY `order`, line_no" in sql
    common.write_json(root / "contracts" / "sales.contract.json", c)
    land.write_queries(root, "sales", pq_source="database")
    assert "ORDER BY [order], [line_no]" in text_of(root, "sales.pq.m") and "`" not in text_of(root, "sales.pq.m")
    assert "AS `order`" in text_of(root, "sales.sql")


@pytest.mark.parametrize("system, quoted", [
    ("PostgreSQL", '""order""'), ("Oracle", '""order""'), ("Snowflake", '""order""'),
    ("BigQuery", "`order`"), ("MySQL", "`order`")])
def test_database_query_quotes_names_for_each_connector(system: str, quoted: str) -> None:
    m = land.m_text(_reserved(system))           # inside the M text literal a double quote is doubled
    assert f"invoice_id AS {quoted}" in m, m
    if quoted.startswith("`"):
        assert "[order]" not in m
    else:
        assert "`" not in m


# ----------------------------------------------------------------------------- supporting changes

def test_control_total_is_exact_with_tolerance_zero(root: Path, tmp_path: Path) -> None:
    """Supporting A-aera-4: no term that grows with the total."""
    c = contract()
    c["expect"]["totals"][0].update(value=1_000_000_000_000, tolerance=0)
    common.write_json(root / "contracts" / "sales.contract.json", agree(c))
    body = rows()[:2]
    body[0][5], body[1][5] = "999999999000", "500"            # 999,999,999,500
    src = write_csv(tmp_path / "big.csv", body)
    rep = land.land_csv(root, "sales", src)
    total = [f for f in rep.findings if f.rule == "L-TOTAL"]
    assert rep.exit_code == 1 and total, [f.line() for f in rep.findings]
    assert "999,999,999,500" in total[0].message and "difference -500" in total[0].message
    assert not (root / "raw" / "sales.csv").exists()
    c["expect"]["totals"][0]["tolerance"] = 500
    common.write_json(root / "contracts" / "sales.contract.json", agree(c))
    assert land.land_csv(root, "sales", src).exit_code == 0


def test_accepted_control_totals_replace_the_contracts_for_their_snapshot(root: Path, tmp_path: Path) -> None:
    """Supporting A-aera-7: raw/<t>.controls.json holds the totals accepted for one snapshot digest."""
    from lib import controls
    body = rows()[:-1]                                         # 71 rows: the contract's total no longer fits
    src = write_csv(tmp_path / "grown.csv", body)
    assert [f.rule for f in land.land_csv(root, "sales", src).findings if f.severity == "error"] == ["L-TOTAL"]
    c = common.read_json(root / "contracts" / "sales.contract.json")
    probe = common.Report("land", "probe")
    typed = land.validate_rows({**c, "expect": {}}, HEADER, body, probe, source="grown.csv")
    types = [col["type"] for col in c["columns"]]
    digest = manifest.raw_digest(HEADER, [[land.snapshot_text(v, types[i]) for i, v in enumerate(r)] for r in typed],
                                 types)
    accepted = sum(float(r[5]) for r in body)
    controls.record_acceptance(root, "sales", digest=digest, rows=len(body),
                               totals=[{"column": "amount", "value": accepted, "tolerance": 0}],
                               given=[f"sales.amount={accepted:.0f}"])
    contract_before = (root / "contracts" / "sales.contract.json").read_bytes()
    rep = land.land_csv(root, "sales", src)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert any("raw/sales.controls.json" in n for n in rep.notes)
    assert (root / "contracts" / "sales.contract.json").read_bytes() == contract_before
    other = write_csv(tmp_path / "other.csv", rows()[:-2])     # another snapshot: the contract's total again
    rep = land.land_csv(root, "sales", other, force=True)
    assert [f.rule for f in rep.findings if f.severity == "error"] == ["L-TOTAL"]
    assert "the contract's control total" in [f for f in rep.findings if f.rule == "L-TOTAL"][0].message


@pytest.mark.parametrize("value, refused", [("x" * 32767, False), ("x" * 32768, True),
                                            ("\U0001F600" * 16384, True)], ids=["32767", "32768", "emoji_32768"])
def test_text_longer_than_a_cell_is_refused_and_the_snapshot_stays(root: Path, tmp_path: Path, value: str,
                                                                   refused: bool) -> None:
    """Supporting A-aera-10: Excel holds at most 32,767 characters (UTF-16 code units) in a cell."""
    land.land_csv(root, "sales", DATA / "sales.csv")
    before = (root / "raw" / "sales.csv").read_bytes()
    body = rows()
    body[1][4] = value
    rep = land.land_csv(root, "sales", write_csv(tmp_path / "long.csv", body), force=True)
    found = [f for f in rep.findings if f.rule == "L-LENGTH"]
    if refused:
        assert rep.exit_code == 1 and found and found[0].severity == "error"
        assert "column product" in found[0].message and "line 3 (32,768 characters)" in found[0].message
        assert (root / "raw" / "sales.csv").read_bytes() == before
    else:
        assert rep.exit_code == 0 and not found
        assert (root / "raw" / "sales.csv").read_bytes() != before


def test_status_prints_the_check_counts_in_checks_words(tmp_path: Path) -> None:
    """Supporting J-4 (d): 'checks: H of N hold; alerts: S of A show a value'."""
    root = tmp_path / "m"
    modelfolder.init_model(root)
    modelfolder.record_step(root, "model.xlsx", "check", {"exit_code": 0, "checks": 21, "failed": 0, "alerts": 2,
                                                         "alerts_shown": 1})
    out = cli("status", str(root)).stdout
    assert "checks: 21 of 21 hold; alerts: 1 of 2 show a value" in out, out
    modelfolder.record_step(root, "model.xlsx", "check", {"exit_code": 1, "checks": 8, "failed": 1})
    out = cli("status", str(root)).stdout
    assert "checks: 7 of 8 hold" in out and "alerts" not in out, out
    modelfolder.record_step(root, "model.xlsx", "check", {"exit_code": 0, "checks": 8, "failed": None})
    assert "checks: 8, not recalculated" in cli("status", str(root)).stdout


# ----------------------------------------------------------------------------- A-aera-14, C-sol-11 (Excel)

EXAMPLE = SKILL / "examples" / "sales_vs_target"


def _copy_model(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "reports", "*.xlsx", "*.manifest.json",
                                                             "*.bak_*"))
    return dest


def _check_states(rep) -> dict[str, str]:
    return {c["id"]: c["state"] for c in rep.data["checks"]}


@pytest.mark.excel
def test_example_check_catches_a_wrong_variance_percentage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import build, check
    monkeypatch.setenv("ROTM_EXAMPLE_ROOTS", str(tmp_path))
    dest = _copy_model(EXAMPLE, tmp_path / "wrong_pct")
    spec = common.read_json(dest / "model.spec.json")
    calc = next(s for s in spec["sheets"] if s["name"] == "calc")
    pct = next(b for b in calc["blocks"] if b["id"] == "variance_pct")
    pct["cell"] = "{g:variance}-{g:variance}"
    common.write_json(dest / "model.spec.json", spec)
    assert build.build(dest / "model.spec.json").exit_code == 0
    rep = check.check_model(dest)
    states = _check_states(rep)
    assert rep.data["recalculated"] is True
    assert states["pct_grid_ties"] == "fail", states
    assert rep.exit_code == 1


@pytest.mark.excel
@pytest.mark.parametrize("folder", [EXAMPLE], ids=lambda p: p.name)
def test_changed_specification_builds_audits_and_holds_every_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                    folder: Path) -> None:
    from lib import audit, build, check
    monkeypatch.setenv("ROTM_EXAMPLE_ROOTS", str(tmp_path))
    dest = _copy_model(folder, tmp_path / folder.name)
    rep = build.build(dest / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert audit.audit_workbook(dest / "model.xlsx", strict=True, record=False).exit_code == 0
    rep = check.check_model(dest, strict=True)
    states = _check_states(rep)
    assert rep.exit_code == 0 and rep.data["recalculated"] is True, [f.line() for f in rep.findings]
    errors = {c["id"]: c["state"] for c in rep.data["checks"] if c.get("severity", "error") == "error"}
    assert set(errors.values()) == {"pass"}, states
    assert audit.audit_workbook(dest / "model.xlsx", strict=True, record=False).exit_code == 0

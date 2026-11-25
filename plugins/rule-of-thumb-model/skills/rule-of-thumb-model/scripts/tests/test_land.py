"""Tests of lib/land.py (design sections 7, 15; P2): CSV validation against the agreed contract, the normalised
snapshot, the SQL and Power Query M texts, refusals, and the Power Query attach. Owner: M3."""
from __future__ import annotations

import csv
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data" / "build"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import common, land, manifest, modelfolder, schemas  # noqa: E402

HEADER = ["invoice_id", "line_no", "inv_date", "region", "product", "amount"]


def contract(**changes) -> dict:
    c = json.loads((DATA / "sales.contract.json").read_text(encoding="utf-8"))
    c.update(changes)
    return c


def agree(c: dict) -> dict:
    c = {k: v for k, v in c.items() if k != "agreement"}
    c["agreement"] = {"status": "agreed", "contract_sha256": modelfolder.contract_hash(c), "reply": "ok, agreed",
                      "by": "tester", "at": "2026-11-25T02:00:00+09:00", "channel": "session", "source_file": None,
                      "recorded_by": "test"}
    return c


@pytest.fixture
def root(outside_tmp: Path) -> Path:
    # v5: outside the plugin wherever --basetemp points (conftest.py), so the 'example' channel is refused here
    r = outside_tmp / "m"
    modelfolder.init_model(r, name="land_test")
    common.write_json(r / "contracts" / "sales.contract.json", agree(contract()))
    return r


def rows() -> list[list[str]]:
    with open(DATA / "sales.csv", encoding="utf-8", newline="") as f:
        return list(csv.reader(f))[1:]


def write_csv(path: Path, body: list[list[str]], header: list[str] = HEADER, encoding: str = "utf-8") -> Path:
    with open(path, "w", encoding=encoding, newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerow(header)
        w.writerows(body)
    return path


def with_total(r: Path, body: list[list[str]]) -> None:
    """Set the contract's control total to the sum of `body` and agree it again."""
    c = contract()
    c["expect"]["totals"][0]["value"] = sum(float(x[5]) for x in body)
    common.write_json(r / "contracts" / "sales.contract.json", agree(c))


# ----------------------------------------------------------------------------- values

@pytest.mark.parametrize("text, typ, value", [
    ("00123", "text", "00123"), ("", "text", None), ("12", "integer", 12), ("12.0", "integer", 12),
    ("-3", "integer", -3), ("1234.5", "number", 1234.5), ("1e3", "number", 1000.0),
    ("2026-01-31", "date", dt.date(2026, 1, 31)), ("2026/1/5", "date", dt.date(2026, 1, 5)),
    ("2026-01-31 00:00:00", "date", dt.date(2026, 1, 31)),
    ("2026-01-31 10:20:30", "datetime", dt.datetime(2026, 1, 31, 10, 20, 30)),
    ("2026-01-31T10:20", "datetime", dt.datetime(2026, 1, 31, 10, 20)),
    ("2026-01-31 10:20:30 UTC", "datetime", dt.datetime(2026, 1, 31, 10, 20, 30)),
    ("TRUE", "boolean", True), ("0", "boolean", False), ("", "number", None),
])
def test_parse_value_accepts(text: str, typ: str, value: object) -> None:
    v, prob = land.parse_value(text, typ)
    assert prob is None and v == value


@pytest.mark.parametrize("text, typ, needle", [
    ("12.5", "integer", "not a whole number"), ("1,234", "integer", "thousands separator"),
    ("1,234.5", "number", "thousands separator"), ("abc", "number", "not a number"), ("inf", "number", "not"),
    ("31/01/2026", "date", "YYYY-MM-DD"), ("2026-02-30", "date", "not a real date"), ("yes", "boolean", "TRUE or"),
    ("2026-01-31 10:00 +09:00", "datetime", "local time"), ("a\x0bb", "text", "control character"),
])
def test_parse_value_refuses(text: str, typ: str, needle: str) -> None:
    v, prob = land.parse_value(text, typ)
    assert v is None and prob and needle in prob


# ----------------------------------------------------------------------------- landing

def test_land_writes_a_normalised_snapshot_and_the_queries(root: Path, tmp_path: Path) -> None:
    body = rows()
    body[0][2] = "2026/01/28"              # normalised to ISO
    body[1][5] = "584000.0"                # normalised to 584000
    src = write_csv(tmp_path / "in.csv", body, encoding="utf-8-sig")
    rep = land.land_csv(root, "sales", src)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    snap = (root / "raw" / "sales.csv").read_text(encoding="utf-8")
    assert not snap.startswith("\ufeff") and "\r" not in snap
    lines = snap.splitlines()
    assert lines[0] == ",".join(HEADER)
    assert lines[1].startswith("000101,1,2026-01-28,") and lines[2].endswith(",584000")
    assert rep.data["rows"] == 72 and rep.data["digest"].startswith("sha256:")
    digest, n, _h = manifest.csv_digest(root / "raw" / "sales.csv", ["text", "integer", "date", "text", "text",
                                                                      "number"])
    assert digest == rep.data["digest"] and n == 72
    assert (root / "queries" / "sales.sql").is_file() and (root / "queries" / "sales.pq.m").is_file()
    st = modelfolder.read_status(root)
    assert st["workbooks"]["model.xlsx"]["land"]["rows"] == 72


def test_same_rows_again_change_nothing_and_other_rows_need_force(root: Path, tmp_path: Path) -> None:
    src = DATA / "sales.csv"
    assert land.land_csv(root, "sales", src).exit_code == 0
    rep = land.land_csv(root, "sales", src)
    assert rep.exit_code == 0 and rep.data["changed"] is False
    body = rows()[:-1]
    with_total(root, body)
    other = write_csv(tmp_path / "other.csv", body)
    with pytest.raises(common.Refusal) as ei:
        land.land_csv(root, "sales", other)
    assert "holds other rows" in ei.value.message and "--force" in (ei.value.fix or "")
    rep = land.land_csv(root, "sales", other, force=True)
    assert rep.exit_code == 0 and rep.data["rows"] == 71


def _land_errors(root: Path, path: Path) -> list[str]:
    rep = land.land_csv(root, "sales", path)
    assert rep.exit_code == 1
    assert not (root / "raw" / "sales.csv").exists(), "nothing is written when the CSV does not fit"
    assert any("nothing was written" in n for n in rep.notes)
    return [f"{f.rule}: {f.message} Fix: {f.fix}" for f in rep.findings if f.severity == "error"]


def test_wrong_column_order_is_an_error(root: Path, tmp_path: Path) -> None:
    body = [[r[1], r[0]] + r[2:] for r in rows()]
    errs = _land_errors(root, write_csv(tmp_path / "x.csv", body, ["line_no", "invoice_id"] + HEADER[2:]))
    assert errs and "in another order" in errs[0] and "invoice_id, line_no, inv_date" in errs[0]


def test_missing_and_extra_columns_are_named(root: Path, tmp_path: Path) -> None:
    body = [r[:5] + ["x"] for r in rows()]
    errs = _land_errors(root, write_csv(tmp_path / "x.csv", body, HEADER[:5] + ["Amount_x"]))
    assert "missing amount" in errs[0] and "not in the contract Amount_x" in errs[0]
    errs = _land_errors(root, write_csv(tmp_path / "y.csv", rows(), HEADER[:5] + ["Amount"]))
    assert "only in upper and lower case: Amount" in errs[0]


def test_bad_values_nulls_allowed_keys_rows_and_totals(root: Path, tmp_path: Path) -> None:
    body = rows()
    body[0][1] = "1.5"          # integer
    body[1][2] = "31/01/2026"   # date
    body[2][3] = "Central"      # not allowed
    body[3][5] = ""             # never null
    body[5] = list(body[4])     # duplicate key
    errs = _land_errors(root, write_csv(tmp_path / "x.csv", body))
    rules = sorted({e.split(":")[0] for e in errs})
    assert rules == ["L-ALLOWED", "L-KEY", "L-NULL", "L-TOTAL", "L-TYPE"]
    text = "\n".join(errs)
    assert "line 2" in text and "line 3" in text and "'Central'" in text and "lines 6 and 7" in text
    assert "control total is 31,435,000" in text


def test_key_repeat_fix_names_the_lines_and_keys_are_exact_text(root: Path, tmp_path: Path) -> None:
    """Supporting A-aera-9: keys compare as exact, case-sensitive text (common.key_tuple); the L-KEY fix names the
    lines and tells the user to look at the rows, not to remove them."""
    body = rows()
    body[5] = list(body[4])                                  # a true repeat: lines 6 and 7
    body[8][0], body[9][0] = "INV9", "inv9"                  # differ only in case: distinct keys
    body[9][1] = body[8][1]
    body[10][0], body[11][0] = "001", "1"                    # text, never read as a number: distinct keys
    body[11][1] = body[10][1]
    with_total(root, body)
    errs = _land_errors(root, write_csv(tmp_path / "x.csv", body))
    key = [e for e in errs if e.startswith("L-KEY")]
    assert len(key) == 1 and "1 row(s) repeat an earlier key" in key[0], errs
    assert "without regard to case" not in key[0]
    fix = key[0].split(" Fix: ", 1)[1]
    assert "lines 6 and 7" in fix and "known exceptions" in fix and "have the source corrected" in fix
    assert "Do not remove or merge rows" in fix and "de-duplicate" not in fix


def test_row_count_outside_the_contract(root: Path, tmp_path: Path) -> None:
    c = contract()
    c["expect"] = {"rows": {"exact": 10}}
    common.write_json(root / "contracts" / "sales.contract.json", agree(c))
    errs = _land_errors(root, DATA / "sales.csv")
    assert errs == [e for e in errs if e.startswith("L-ROWS")] and "expects exactly 10" in errs[0]


def test_empty_extract_needs_allow_empty(root: Path, tmp_path: Path) -> None:
    c = contract()
    c["expect"] = {"rows": {"min": 0, "max": 10}}
    common.write_json(root / "contracts" / "sales.contract.json", agree(c))
    errs = _land_errors(root, write_csv(tmp_path / "e.csv", []))
    assert errs[0].startswith("L-EMPTY")


def test_ragged_rows_are_an_error(root: Path, tmp_path: Path) -> None:
    body = rows()
    body[3] = body[3][:4]
    errs = _land_errors(root, write_csv(tmp_path / "x.csv", body))
    assert errs[0].startswith("L-FIELDS") and "lines 5" in errs[0]


def test_long_integer_ids_warn(root: Path, tmp_path: Path) -> None:
    c = contract()
    c["columns"][1]["type"] = "integer"
    body = rows()
    body[0][1] = "1234567890123456"
    c["expect"] = {"rows": {"min": 1, "max": 1000}}
    common.write_json(root / "contracts" / "sales.contract.json", agree(c))
    rep = land.land_csv(root, "sales", write_csv(tmp_path / "x.csv", body))
    assert rep.exit_code == 0 and [f.rule for f in rep.findings] == ["L-DIGITS"]


@pytest.mark.parametrize("state, needle", [("proposed", "proposed, not agreed"), ("stale", "was changed after"),
                                           ("missing", "has no contract"), ("example", "'example' agreement")])
def test_land_refuses_a_contract_that_is_not_agreed(root: Path, state: str, needle: str) -> None:
    path = root / "contracts" / "sales.contract.json"
    c = agree(contract())
    if state == "proposed":
        c["agreement"] = {"status": "proposed"}
    elif state == "stale":
        c["grain"] = "changed after agreement"
    elif state == "missing":
        path.unlink()
    else:
        c["agreement"]["channel"] = "example"
    if state != "missing":
        common.write_json(path, c)
    with pytest.raises(common.Refusal) as ei:
        land.land_csv(root, "sales", DATA / "sales.csv")
    assert needle in ei.value.message
    assert not (root / "raw" / "sales.csv").exists()
    with pytest.raises(common.Refusal):
        land.write_queries(root, "sales")
    assert not (root / "queries" / "sales.sql").exists()


def test_not_a_model_folder(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    with pytest.raises(common.Refusal) as ei:
        land.land_csv(tmp_path / "plain", "sales", DATA / "sales.csv")
    assert "not a model folder" in ei.value.message and "rotm.py init" in (ei.value.fix or "")


def test_encoding_refusal_and_option(root: Path, tmp_path: Path) -> None:
    c = contract()
    c["columns"][4]["allowed"] = None
    common.write_json(root / "contracts" / "sales.contract.json", agree(c))
    body = rows()
    body[0][4] = "제품"
    src = write_csv(tmp_path / "kr.csv", body, encoding="cp949")
    with pytest.raises(common.Refusal) as ei:
        land.land_csv(root, "sales", src)
    assert "--encoding cp949" in (ei.value.fix or "")
    rep = land.land_csv(root, "sales", src, encoding="cp949")
    assert rep.exit_code == 0
    assert "제품" in (root / "raw" / "sales.csv").read_text(encoding="utf-8")


def test_read_snapshot_types_values(root: Path) -> None:
    land.land_csv(root, "sales", DATA / "sales.csv")
    snap = land.read_snapshot(root, "sales")
    assert snap.columns == HEADER and snap.types == ["text", "integer", "date", "text", "text", "number"]
    assert snap.rows[0][:3] == ("000101", 1, dt.date(2026, 1, 28)) and isinstance(snap.rows[0][5], float)
    assert any(r[4] is None for r in snap.rows)          # empty product is null
    assert snap.digest == manifest.csv_digest(root / "raw" / "sales.csv", snap.types)[0]


# ----------------------------------------------------------------------------- query texts

def test_sql_text_follows_the_contract() -> None:
    sql = land.sql_text(agree(contract()))
    assert "Contract agreed by tester on 2026-11-25T02:00:00+09:00" in sql
    body = [line for line in sql.splitlines() if not line.startswith("--")]
    assert body[0] == "SELECT"
    assert body[1] == "  CAST(i.invoice_id AS STRING) AS invoice_id,"
    assert body[-3].startswith("FROM proj.ds.invoice_lines l JOIN")
    assert body[-2].startswith("WHERE i.posted")
    assert body[-1] == "ORDER BY invoice_id, line_no"


def test_sql_text_quotes_names_and_marks_a_missing_source() -> None:
    c = contract(source=None)
    c["columns"][0]["name"] = "order"
    c["columns"][0]["source_expression"] = None
    c["columns"][3]["name"] = "지역"
    c["columns"][3]["source_expression"] = None
    c["key"] = ["order", "line_no"]
    sql = land.sql_text(c)
    assert "  `order`," in sql and "  `지역`," in sql
    assert "FROM <source table>" in sql and "replace <source table>" in sql
    assert "ORDER BY `order`, line_no" in sql


def test_m_text_local_csv() -> None:
    m = land.m_text(contract(), csv_path="C:/models/sales/raw/sales.csv")
    assert m.startswith("let\n") and m.rstrip().endswith("in\n    Typed")
    assert 'File.Contents("C:\\models\\sales\\raw\\sales.csv")' in m
    assert "Encoding = 65001" in m and "Table.PromoteHeaders" in m
    assert 'Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue' in m
    assert '{"line_no", Int64.Type}' in m and '{"inv_date", type date}' in m and '"en-US")' in m


def test_m_text_database_template() -> None:
    m = land.m_text(agree(contract()))
    assert 'GoogleBigQuery.Database(){[Name = "<billing project>"]}[Data]' in m
    assert "Value.NativeQuery(Target, \"SELECT#(lf)" in m and "[EnableFolding = true]" in m
    assert "rotm.py did not connect to any database" in m
    assert "'2026-01-01'" in m                              # SQL text kept inside the M string
    c = agree(contract())
    c["source"]["system"] = "MySQL"
    assert "[EnableFolding = true]" not in land.m_text(c)    # only the systems Microsoft lists (platforms B1.4)
    c["source"]["system"] = "Teradata"
    assert "names no known system" in land.m_text(c)
    c["columns"][0]["source_expression"] = 'CONCAT("A", x)'
    assert 'CONCAT(""A"", x)' in land.m_text(c)              # quotes doubled inside the M string


def test_write_queries_keeps_files_edited_after_this_agreement(root: Path) -> None:
    written = land.write_queries(root, "sales")
    assert {p.name for p in written} == {"sales.sql", "sales.pq.m"}
    sql = root / "queries" / "sales.sql"
    stamped = sql.read_text(encoding="utf-8")
    assert common.read_query_stamp(stamped)["edited"] is False
    sql.write_text(stamped + "-- edited by the agent after the agreement\n", encoding="utf-8")
    assert land.write_queries(root, "sales") == []             # the stamp names the agreed contract: kept
    assert sql.read_text(encoding="utf-8").endswith("-- edited by the agent after the agreement\n")
    sql.write_text("-- no stamp\nSELECT 1\n", encoding="utf-8")
    with pytest.raises(common.Refusal) as ei:                    # no stamp: the contract it was written for is unknown
        land.write_queries(root, "sales")
    assert "queries/sales.sql has no rotm-stamp line" in ei.value.message
    land.write_queries(root, "sales", overwrite=True, pq_source="database")
    assert "Value.NativeQuery" in (root / "queries" / "sales.pq.m").read_text(encoding="utf-8")


def test_generated_m_text_of_another_folder_is_written_again(root: Path, tmp_path: Path) -> None:
    other = land.m_text(contract(), csv_path=tmp_path / "template" / "raw" / "sales.csv")   # an unstamped v1 text
    (root / "queries").mkdir(exist_ok=True)
    (root / "queries" / "sales.pq.m").write_text(other, encoding="utf-8")
    written = land.write_queries(root, "sales")
    assert root / "queries" / "sales.pq.m" in written
    text = (root / "queries" / "sales.pq.m").read_text(encoding="utf-8")
    assert 'File.Contents("<model folder>\\raw\\sales.csv")' in text and str(tmp_path) not in text
    assert list((root / "queries").glob("sales.pq.m.bak_*")), "the replaced text is kept"
    assert land.write_queries(root, "sales") == []                 # now current: kept


# ----------------------------------------------------------------------------- CLI and Power Query attach

def cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    import os
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "land", *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=180, env=e)


def test_cli_queries_only_then_land(root: Path) -> None:
    proc = cli(str(root), "--table", "sales")
    assert proc.returncode == 0, proc.stderr
    assert "wrote queries/sales.sql" in proc.stdout and "Connected Sheets" in proc.stdout
    proc = cli(str(root), "--table", "sales", "--csv", str(DATA / "sales.csv"), "--json", "-")
    assert proc.returncode == 0, proc.stderr
    rep = json.loads(proc.stdout)
    assert rep["data"]["rows"] == 72 and schemas.validate(rep, "report") == []


def test_cli_refusal_and_findings_exit_codes(root: Path, tmp_path: Path) -> None:
    body = rows()
    body[0][3] = "Central"
    proc = cli(str(root), "--table", "sales", "--csv", str(write_csv(tmp_path / "x.csv", body)))
    assert proc.returncode == 1 and "L-ALLOWED" in proc.stdout
    proc = cli(str(root), "--table", "nope")
    assert proc.returncode == 2 and "has no contract" in proc.stderr and "Traceback" not in proc.stderr


def test_attach_needs_a_built_workbook_and_excel(root: Path) -> None:
    land.land_csv(root, "sales", DATA / "sales.csv")
    with pytest.raises(common.Refusal) as ei:
        land.attach_power_query(root / "model.xlsx", root, "sales")
    assert "build the workbook" in ei.value.message
    from lib import build
    shutil.copy(DATA / "model.spec.json", root / "model.spec.json")
    build.build(root / "model.spec.json")
    proc = cli(str(root), "--table", "sales", "--power-query", env={"ROTM_NO_EXCEL": "1"})
    assert proc.returncode == 3 and "Excel is not available" in proc.stderr
    land.write_queries(root, "sales", overwrite=True, pq_source="database")
    with pytest.raises(common.Refusal) as ei:
        land.attach_power_query(root / "model.xlsx", root, "sales")
    assert "placeholders" in ei.value.message


def test_build_without_excel_warns_for_power_query_landing(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import build
    land.land_csv(root, "sales", DATA / "sales.csv")
    spec = json.loads((DATA / "model.spec.json").read_text(encoding="utf-8"))
    for s in spec["sheets"]:
        if s["type"] == "raw":
            s["landing"] = "power_query"
    common.write_json(root / "model.spec.json", spec)
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 1 and [f.rule for f in rep.findings] == ["W-PQ-NOT-ATTACHED"]
    assert "land" in rep.findings[0].fix and "--power-query" in rep.findings[0].fix
    rep = build.build(root / "model.spec.json", power_query=False)
    assert "--no-power-query" in rep.findings[0].message


@pytest.mark.excel
def test_attach_power_query_through_excel(root: Path) -> None:
    from lib import build, excel
    land.land_csv(root, "sales", DATA / "sales.csv")
    shutil.copy(DATA / "model.spec.json", root / "model.spec.json")
    build.build(root / "model.spec.json")
    rep = land.attach_power_query(root / "model.xlsx", root, "sales")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert rep.data["rows"] == 72 and not excel.pid_alive(rep.data["pid"])
    data = manifest.load_manifest(root / "model.xlsx")
    entry = manifest.raw_sheet(data, "sales")
    assert entry["landing"] == "power_query" and entry["query_name"] == "q_sales"
    assert manifest.sheet_digest(root / "model.xlsx", "raw_sales", entry["types"])[0] == entry["digest"]
    from lib import workbook
    parts = workbook.package_parts(root / "model.xlsx")
    assert "q_sales" in parts.query_names
    rebuilt = build.build(root / "model.spec.json", force=True)     # the query is kept on a rebuild
    assert rebuilt.data["power_query_attached"] == ["sales"]
    assert any("keeps its Power Query table" in n for n in rebuilt.notes)

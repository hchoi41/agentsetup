"""v3 tests of landing and Power Query (package P3).

- [A-aera-6]: a fraction of a second is cut, never rounded up, in land, the sheet digest and the CSV digest (a value
  read from a sheet is first rounded to the nearest millisecond); warning L-PRECISION for a number with more than
  15 significant digits, a cut fraction and a dropped Z or UTC offset.
- [A-aera-12, C-sol-9, C-sol-10] part (1): --pq-source naming the other kind than queries/<t>.pq.m holds: written
  again when not edited (note N-PQ-SWITCH), refused when edited or unstamped (F-PQ-SWITCH, exit 2). Parts (2), (3),
  (6) and (7): the texts of excel-power-query.md, examples/sales_vs_target/task.md and google-sheets.md.
- Supporting [A-aera-1, A-aera-4, C-sol-1] and [A-aera-2]: land --power-query writes workbook_digest when the loaded
  rows equal the landed snapshot, and its status record holds input_sha256 and output_sha256.
- Supporting documents of [C-sol-7], [A-aera-5, C-sol-2], [A-aera-8, C-sol-6] and [C-sol-14].
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
DATA = Path(__file__).resolve().parent / "data" / "build"
REFS = SKILL / "references"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import common, land, manifest, modelfolder  # noqa: E402

EV_HEADER = ["event_id", "at", "amount"]
EV_TYPES = ["text", "datetime", "number"]


def agree(c: dict) -> dict:
    c = {k: v for k, v in c.items() if k != "agreement"}
    c["agreement"] = {"status": "agreed", "contract_sha256": modelfolder.contract_hash(c), "reply": "ok, agreed",
                      "by": "tester", "at": "2026-11-25T08:00:00+09:00", "channel": "session", "source_file": None,
                      "recorded_by": "test"}
    return c


def events_contract() -> dict:
    return {
        "rotm_contract": 1, "table": "events", "raw_sheet": "raw_events", "excel_table": "tbl_raw_events",
        "purpose": "events for the v3 landing tests",
        "source": {"connector": "power_query", "system": "BigQuery", "object": "proj.ds.events",
                   "from": "proj.ds.events", "where": None},
        "grain": "one row per event", "key": ["event_id"],
        "columns": [
            {"name": "event_id", "type": "text", "unit": None, "null_rule": "never", "allowed": None},
            {"name": "at", "type": "datetime", "unit": "datetime", "null_rule": "never", "allowed": None},
            {"name": "amount", "type": "number", "unit": "KRW", "null_rule": "never", "allowed": None},
        ],
        "expect": {"rows": {"min": 1, "max": 100}, "totals": []},
        "refresh": {"frequency": "monthly", "capacity_rows": None, "allow_empty": False},
    }


EV_SPEC = {
    "rotm_spec": 1, "name": "events_test", "title": "Events (v3 landing test)",
    "purpose": "the digest of a datetime column", "output": "model.xlsx", "product": "both",
    "limitations": ["test model of tests/test_v3_land.py"],
    "sheets": [
        {"name": "raw_events", "type": "raw", "table": "events", "purpose": "events as landed"},
        {"name": "checks", "type": "checks", "checks": []},
    ],
}


@pytest.fixture
def ev(tmp_path: Path) -> Path:
    r = tmp_path / "ev"
    modelfolder.init_model(r, name="v3_events")
    common.write_json(r / "contracts" / "events.contract.json", agree(events_contract()))
    return r


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "m"
    modelfolder.init_model(r, name="v3_landing")
    c = json.loads((DATA / "sales.contract.json").read_text(encoding="utf-8"))
    common.write_json(r / "contracts" / "sales.contract.json", agree(c))
    return r


def write_events(path: Path, body: list[list[str]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(EV_HEADER)
        w.writerows(body)
    return path


def cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=300, env=e)


def precision(rep) -> list:
    return [f for f in rep.findings if f.rule == "L-PRECISION"]


def snapshot_rows(r: Path, table: str) -> list[list[str]]:
    with open(r / "raw" / f"{table}.csv", encoding="utf-8", newline="") as f:
        return list(csv.reader(f))[1:]


# ----------------------------------------------------------------------------- A-aera-6: the cut, never rounded up

@pytest.mark.parametrize("text, value", [
    ("2026-06-30T23:59:59.900Z", dt.datetime(2026, 6, 30, 23, 59, 59)),
    ("2026-06-30 23:59:59.900", dt.datetime(2026, 6, 30, 23, 59, 59)),
    ("2026-07-01T00:00:00.100Z", dt.datetime(2026, 7, 1, 0, 0, 0)),
    ("2026-12-31T23:59:59.999999", dt.datetime(2026, 12, 31, 23, 59, 59)),
    ("2026-06-30T10:20:30.5+00:00", dt.datetime(2026, 6, 30, 10, 20, 30)),
])
def test_parse_value_cuts_a_fraction_of_a_second(text: str, value: dt.datetime) -> None:
    v, prob = land.parse_value(text, "datetime")
    assert prob is None and v == value


def test_digest_rule_cuts_text_and_reads_a_sheet_value_to_the_millisecond() -> None:
    nv = manifest.normalise_value
    # text: cut as written
    assert nv("2026-06-30T23:59:59.900Z", "datetime") == "2026-06-30T23:59:59"
    assert nv("2026-06-30 23:59:59.9999", "datetime") == "2026-06-30T23:59:59"
    # a datetime read from a sheet: rounded to the millisecond, then cut
    assert nv(dt.datetime(2026, 6, 30, 23, 59, 59, 900_000), "datetime") == "2026-06-30T23:59:59"
    assert nv(dt.datetime(2026, 6, 30, 23, 59, 59, 999_000), "datetime") == "2026-06-30T23:59:59"
    assert nv(dt.datetime(2026, 2, 28, 9, 30, 14, 999_900), "datetime") == "2026-02-28T09:30:15"   # float error
    # an Excel serial: 23:59:59 reads back as 23:59:59, also with the float error of the serial
    serial = (dt.datetime(2026, 6, 30, 23, 59, 59) - manifest.EXCEL_EPOCH).total_seconds() / 86400
    for s in (serial, serial - 2e-10, serial + 2e-10):
        assert nv(s, "datetime") == "2026-06-30T23:59:59", s
    fraction = (dt.datetime(2026, 6, 30, 23, 59, 59, 900_000) - manifest.EXCEL_EPOCH).total_seconds() / 86400
    assert nv(fraction, "datetime") == "2026-06-30T23:59:59"
    assert land.snapshot_text(dt.datetime(2026, 6, 30, 23, 59, 59, 900_000), "datetime") == "2026-06-30T23:59:59"


def test_land_cuts_the_fraction_and_warns_for_the_cut_and_the_dropped_z(ev: Path, tmp_path: Path) -> None:
    src = write_events(tmp_path / "events.csv", [["E1", "2026-06-30T23:59:59.900Z", "100"],
                                                 ["E2", "2026-07-01T00:00:00.100Z", "200"],
                                                 ["E3", "2026-06-30 23:59:59.900", "300"]])
    rep = land.land_csv(ev, "events", src)
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    assert [r[1] for r in snapshot_rows(ev, "events")] == ["2026-06-30T23:59:59", "2026-07-01T00:00:00",
                                                           "2026-06-30T23:59:59"]
    found = {f.data["reason"]: f for f in precision(rep)}
    assert set(found) == {"fraction", "zone"}, [f.line() for f in rep.findings]
    cut, zone = found["fraction"], found["zone"]
    assert cut.severity == "warning" and cut.count == 3 and cut.data["column"] == "at"
    assert cut.message == ("column at: 3 value(s) landed differently from the text (first line 2: "
                           "'2026-06-30T23:59:59.900Z' landed as '2026-06-30T23:59:59'): a fraction of a second was "
                           "cut (never rounded up)")
    assert zone.count == 2 and zone.message.endswith("a Z or UTC offset was dropped; the time is landed as written")
    assert "first line 2" in zone.message
    assert cut.fix == land.PRECISION_FIX and "land the column as text" in cut.fix and "round" in cut.fix
    # the digest of the landed rows is the digest of the snapshot
    assert manifest.csv_digest(ev / "raw" / "events.csv", EV_TYPES)[0] == rep.data["digest"]
    # strict mode fails on the warning, and the command names it
    proc = cli("land", str(ev), "--table", "events", "--csv", str(src), "--strict")
    assert proc.returncode == 1 and "L-PRECISION" in proc.stdout, proc.stdout + proc.stderr


def test_without_z_the_time_lands_on_the_same_day(ev: Path, tmp_path: Path) -> None:
    src = write_events(tmp_path / "events.csv", [["E1", "2026-06-30 23:59:59.900", "100"]])
    rep = land.land_csv(ev, "events", src)
    assert snapshot_rows(ev, "events") == [["E1", "2026-06-30T23:59:59", "100"]]
    assert [f.data["reason"] for f in precision(rep)] == ["fraction"]
    typed = land.read_snapshot(ev, "events").rows
    assert typed[0][1].date() == dt.date(2026, 6, 30)


def test_a_sixteen_digit_amount_warns_naming_its_line(ev: Path, tmp_path: Path) -> None:
    src = write_events(tmp_path / "events.csv", [["E1", "2026-06-30T10:00:00", "100"],
                                                 ["E2", "2026-06-30T11:00:00", "12345678901234.56"],
                                                 ["E3", "2026-06-30T12:00:00", "7"]])
    rep = land.land_csv(ev, "events", src)
    found = precision(rep)
    assert len(found) == 1, [f.line() for f in rep.findings]
    f = found[0]
    assert f.data == {"column": "amount", "reason": "digits", "first_line": 3, "text": "12345678901234.56",
                      "landed": "12345678901234.6"}
    assert f.message == ("column amount: 1 value(s) landed differently from the text (first line 3: "
                         "'12345678901234.56' landed as '12345678901234.6'): more than 15 significant digits; "
                         "Excel and the snapshot keep 15")
    assert snapshot_rows(ev, "events")[1][2] == "12345678901234.6"


def test_values_within_the_limits_give_no_l_precision(ev: Path, tmp_path: Path) -> None:
    body = [["E1", "2026-06-30T23:59:59", "123456789012.345"], ["E2", "2026-06-30 23:59:59.000", "0.1"],
            ["E3", "2026-06-30T23:59", "1234.50"], ["E4", "2026-06-30", "-0"], ["E5", "2026-07-01 00:00:00", "1e20"],
            ["E6", "2026-07-01T00:00:01.000000", "100"], ["E7", "2026-07-01T00:00:02", "999999999999999"]]
    rep = land.land_csv(ev, "events", write_events(tmp_path / "events.csv", body))
    assert rep.counts()["errors"] == 0 and rep.counts()["warnings"] == 0, [f.line() for f in rep.findings]
    for text, typ in (("0.30000000000000004", "number"), ("2026-06-30T23:59:59.5", "datetime")):
        assert land.precision_changes(text, typ), text
    for text, typ in (("0.3", "number"), ("12.50", "number"), ("2026-06-30T23:59:59", "datetime"),
                      ("abc", "text"), ("", "number")):
        assert land.precision_changes(text, typ) == [], text


def _build_events(ev: Path, tmp_path: Path) -> Path:
    from lib import build
    src = write_events(tmp_path / "events.csv", [["E1", "2026-06-30T23:59:59.900Z", "100"],
                                                 ["E2", "2026-07-01T00:00:00.100Z", "200"],
                                                 ["E3", "2026-06-30 12:00:00.999", "300"],
                                                 ["E4", "2026-01-31 00:00:00.001", "400"]])
    land.land_csv(ev, "events", src)
    common.write_json(ev / "model.spec.json", EV_SPEC)
    rep = build.build(ev / "model.spec.json")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    return ev / "model.xlsx"


def test_sheet_digest_of_a_built_workbook_equals_the_csv_digest(ev: Path, tmp_path: Path) -> None:
    wb = _build_events(ev, tmp_path)
    csv_digest = manifest.csv_digest(ev / "raw" / "events.csv", EV_TYPES)[0]
    entry = manifest.raw_sheet(manifest.load_manifest(wb), "events")
    assert entry["digest"] == csv_digest
    assert manifest.sheet_digest(wb, "raw_events", EV_TYPES)[0] == csv_digest
    _h, rows = manifest.sheet_rows(wb, "raw_events")
    assert [r[1] for r in rows] == [dt.datetime(2026, 6, 30, 23, 59, 59), dt.datetime(2026, 7, 1),
                                    dt.datetime(2026, 6, 30, 12, 0, 0), dt.datetime(2026, 1, 31)]


@pytest.mark.excel
def test_sheet_digest_equals_the_csv_digest_after_excel_saves_the_workbook(ev: Path, tmp_path: Path) -> None:
    from lib import excel
    wb = _build_events(ev, tmp_path)
    saved = tmp_path / "saved_by_excel.xlsx"
    res = excel.recalc(wb, out=saved)
    assert res.ok, res
    csv_digest = manifest.csv_digest(ev / "raw" / "events.csv", EV_TYPES)[0]
    assert manifest.sheet_digest(saved, "raw_events", EV_TYPES)[0] == csv_digest


# ----------------------------------------------------------------------------- part (1): --pq-source switch

def _pq(r: Path) -> Path:
    return r / "queries" / "sales.pq.m"


def test_pq_source_switch_rewrites_a_query_not_edited_since_land_wrote_it(root: Path) -> None:
    land.land_csv(root, "sales", DATA / "sales.csv")
    assert land._m_kind(_pq(root).read_text(encoding="utf-8")) == "csv"
    agreed = common.read_json(root / "contracts" / "sales.contract.json")["agreement"]["contract_sha256"]
    proc = cli("land", str(root), "--table", "sales", "--pq-source", "database")
    assert proc.returncode == 0, proc.stderr
    text = _pq(root).read_text(encoding="utf-8")
    assert "Value.NativeQuery" in text and "Csv.Document" not in text
    stamp = common.read_query_stamp(text)
    assert stamp["contract_sha256"] == agreed and stamp["edited"] is False
    assert ("queries/sales.pq.m was written for a csv source and not edited since: written again for --pq-source "
            "database") in proc.stdout
    # back to the local CSV, also through land --csv
    rep = land.land_csv(root, "sales", DATA / "sales.csv", pq_source="csv")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert "Csv.Document" in _pq(root).read_text(encoding="utf-8")
    assert any("written for a database source and not edited since: written again for --pq-source csv" in n
               for n in rep.notes), rep.notes
    # the same kind again: kept, no note
    rep = land.land_csv(root, "sales", DATA / "sales.csv", pq_source="csv")
    assert not any("--pq-source" in n for n in rep.notes), rep.notes


def test_pq_source_switch_refuses_an_edited_or_unstamped_query(root: Path) -> None:
    land.write_queries(root, "sales")
    path = _pq(root)
    edited = path.read_text(encoding="utf-8").replace("    Typed\n", "    // reviewed by hand\n    Typed\n")
    path.write_text(edited, encoding="utf-8")
    before = path.read_bytes()
    for extra in ((), ("--keep-queries",), ("--csv", str(DATA / "sales.csv"))):
        proc = cli("land", str(root), "--table", "sales", "--pq-source", "database", *extra)
        assert proc.returncode == 2, (extra, proc.stdout, proc.stderr)
        assert "F-PQ-SWITCH" in proc.stderr and "Traceback" not in proc.stderr
        assert ("queries/sales.pq.m holds a csv query edited by hand, and --pq-source asks for database; nothing was "
                "written") in proc.stderr
        assert "--table sales --rewrite-queries --pq-source database" in proc.stderr
        assert path.read_bytes() == before, extra
        assert not (root / "raw" / "sales.csv").exists(), "nothing was landed"
    # without a stamp
    path.write_text(edited.split("\n", 1)[1], encoding="utf-8")
    with pytest.raises(common.Refusal) as ei:
        land.write_queries(root, "sales", pq_source="database")
    assert ei.value.key == "F-PQ-SWITCH" and "holds a csv query without a stamp" in ei.value.message
    # the command the refusal names writes it again and keeps the old file
    proc = cli("land", str(root), "--table", "sales", "--rewrite-queries", "--pq-source", "database")
    assert proc.returncode == 0, proc.stderr
    assert "Value.NativeQuery" in path.read_text(encoding="utf-8")
    baks = list((root / "queries").glob("sales.pq.m.bak_*"))
    assert len(baks) == 1 and "Csv.Document" in baks[0].read_text(encoding="utf-8")
    # a database query with its placeholders filled in is refused for --pq-source csv
    filled = path.read_text(encoding="utf-8").replace("<billing project>", "my-project")
    path.write_text(filled, encoding="utf-8")
    with pytest.raises(common.Refusal, match="holds a database query edited by hand, and --pq-source asks for csv"):
        land.write_queries(root, "sales", pq_source="csv")
    assert path.read_text(encoding="utf-8") == filled
    assert land.write_queries(root, "sales", pq_source="database") == [], "the same kind: kept"


# ----------------------------------------------------------------------------- supporting: the attach records

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


def _set_workbook_digest(wb: Path, value: str | None) -> None:
    data = manifest.load_manifest(wb)
    entry = manifest.raw_sheet(data, "sales")
    if value is None:
        entry.pop("workbook_digest", None)
    else:
        entry["workbook_digest"] = value
    manifest.write_manifest(wb, data)


OTHER = "sha256:" + "0" * 64


def test_attach_records_the_workbook_digest_and_both_hashes(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import excel
    wb = _built(root)
    _set_workbook_digest(wb, OTHER)
    monkeypatch.setattr(excel, "excel_status", lambda *a, **k: excel.ExcelStatus(True, "test"))
    monkeypatch.setattr(excel, "pq_attach", lambda workbook, **kw: _FakeJob())
    before = common.file_sha256(wb)
    rep = land.attach_power_query(wb, root, "sales")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    entry = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    assert entry["workbook_digest"] == entry["digest"]
    rec = modelfolder.read_status(root)["workbooks"]["model.xlsx"]["land"]
    assert rec["input_sha256"] == before and rec["output_sha256"] == common.file_sha256(wb) == rec["sha256"]
    # loaded rows that differ from the landed snapshot: workbook_digest is not written
    _set_workbook_digest(wb, OTHER)
    monkeypatch.setattr(manifest, "sheet_digest", lambda *a, **k: ("sha256:" + "1" * 64, 72, []))
    rep = land.attach_power_query(wb, root, "sales")
    assert [f.rule for f in rep.findings] == ["W-PQ-DIGEST"]
    assert manifest.raw_sheet(manifest.load_manifest(wb), "sales")["workbook_digest"] == OTHER


@pytest.mark.excel
def test_attach_through_excel_records_the_workbook_digest_and_both_hashes(root: Path) -> None:
    wb = _built(root)
    _set_workbook_digest(wb, None)
    before = common.file_sha256(wb)
    rep = land.attach_power_query(wb, root, "sales")
    assert rep.exit_code == 0 and rep.data["rows"] == 72, [f.line() for f in rep.findings]
    entry = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    assert entry["landing"] == "power_query"
    assert entry["workbook_digest"] == entry["digest"] == manifest.sheet_digest(wb, "raw_sales", entry["types"])[0]
    rec = modelfolder.read_status(root)["workbooks"]["model.xlsx"]["land"]
    assert rec["input_sha256"] == before
    assert rec["output_sha256"] == rec["sha256"] == common.file_sha256(wb) != before


# ----------------------------------------------------------------------------- the documents

def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    return text[start:nxt if nxt > 0 else len(text)]


def test_power_query_reference_gives_the_database_sequence() -> None:
    text = _text(REFS / "excel-power-query.md")
    assert "\n## Database source: the sequence\n" in text
    sec = _section(text, "## Database source: the sequence")
    for needle in ("contracts/<t>.contract.md", "queries/<t>.sql", "--csv", "--pq-source database", "<server>",
                   "--keep-queries", "--power-query", "Require user approval for new native database queries",
                   "check \"<model>\" --refresh", "--accept-refresh --control <t>.<column>=<source total>",
                   "No database was reached"):
        assert needle in sec, needle
    assert sec.count("\n1. ") == 1 and "\n8. " in sec, "a numbered sequence"


def test_power_query_reference_says_a_rename_keeps_the_old_sheet() -> None:
    text = _text(REFS / "excel-power-query.md")
    assert "every formula that reads it would turn into `#REF!`" not in text
    assert "Deleting or renaming the raw sheet to make room for the query: every reference to it becomes" not in text
    for sec in (_section(text, "## Attaching a query by hand"), _section(text, "## Common mistakes")):
        assert "#REF!" in sec and "Excel rewrites the formulas to the new name" in sec
    hand = _section(text, "## Attaching a query by hand")
    assert "q_<t>" in hand and "=INDEX(raw_<t>!$A:$A,A2)" in hand and "<t>_rows" in hand
    assert "check \"<model>\" --accept-refresh --strict" in hand and "--control <t>.<column>=<source total>" in hand
    assert "attached by hand" in hand


def test_power_query_reference_states_the_v3_acceptance_rules() -> None:
    text = _text(REFS / "excel-power-query.md")
    flat = " ".join(text.split())
    # The ids the code prints (a finding's rule or a refusal's key) are named. The plan's labels of notes,
    # message texts and refusals that print no id are given by their words, as in SKILL.md and checks.md
    # (v3 integrator: test_references.py::test_rule_ids_mentioned_exist refuses an id that no module prints).
    for rule in ("D-CONTROL-INPUT", "R-CHANGED", "CT-VERSION", "X-NO-QUERY", "F-PQ-SWITCH"):
        assert rule in text, rule
    for label, words in (("F-REFRESH-STATE", '"nothing was refreshed"'),
                         ("R-NEWER", "accepts nothing and does not change `raw/<t>.csv`"),
                         ("N-NOT-ACCEPTED", "no table is accepted in that run"),
                         ("N-HAND-QUERY", "was not compared with the agreed contract"),
                         ("N-PQ-SWITCH", "not edited since: written again for --pq-source database")):
        assert words in flat, (label, words)
    after = _section(text, "## After a refresh")
    assert "no rows" in after and "row 2" in after


def test_google_cutover_names_the_sheet_the_formulas_read() -> None:
    text = _text(REFS / "google-sheets.md")
    assert "If the three agree, the model reads the extract" not in text
    assert "Nothing in this file was tested in Google Sheets." in text
    cut = _section(text, "## Cutover when the extract is on a new sheet")
    assert "first fetch cell" in cut and "h_<t>" in cut and "<t>_rows" in cut
    assert "row-level comparison" in cut and "still reads the static snapshot" in cut
    kept = _section(text, "## Models kept in Google Sheets")
    assert "holds no workbook" in kept and "new workbook name" in kept and "existing workbook" in kept


def test_example_task_says_the_snapshots_stand_in_for_the_queries() -> None:
    text = _text(SKILL / "examples" / "sales_vs_target" / "task.md")
    assert "static snapshots" in text and "without Excel" in text
    assert "\n## Power Query variant\n" in text
    part = _section(text, "## Power Query variant")
    assert '"landing": "power_query"' in part and "build" in part and "--refresh" in part
    spec = common.read_json(SKILL / "examples" / "sales_vs_target" / "model.spec.json")
    assert all(s.get("landing", "static") == "static" for s in spec["sheets"] if s["type"] == "raw")

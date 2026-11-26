"""Tests of the v7 change of package P1 to the contract and to landing (round 6 verdict, [C-sol-8] and [A-aera-6]).

[C-sol-8]: a CSV whose header holds the headers the contract was proposed from can be landed.
- `contract propose --csv` records for each column the sample header it was read from (`csv_header`, trimmed); the
  contract .md shows the column "CSV header" beside "Column" when a header differs from its name, and "Points to
  confirm" lists each header with its destination name. `--columns` records none.
- `land --csv` (also with --force and --control): a header equal to the destination names lands as in v6. A header
  that holds every recorded sample header exactly once (after trimming) and no other column is read by that mapping,
  and raw/<t>.csv gets the destination names in the contract's order; land prints each header with its name.
  Anything else is L-COLUMNS with nothing written; its fix names both header lines land accepts.
- Contracts without the field (every shipped contract) keep their hash and their snapshot, and their .md is the text
  rendered from the JSON (v7 integrator: the shipped .md files were written again with contract show for the
  sample profile's minimum and maximum, [A-aera-6]).

[A-aera-6]: every number of the contract .md is the shortest text that gives the stored number, with thousands
separators in the whole part, and scientific notation below 0.000001.

Model folders lie outside the plugin (fixture outside_tmp); the commands run with ROTM_NO_EXCEL=1.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, land, modelfolder

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
BY = "Owner"
TABLE = "orders"
HEADER = ["Order ID", "Order Date", "Net Sales"]
NAMES = ["order_id", "order_date", "net_sales"]
ROWS = [["A1", "2026-01-05", "10.5"], ["A2", "2026-01-06", "20"], ["A3", "2026-01-07", "30"]]
TYPES = ["text", "date", "number"]
REPLY = "The orders contract is agreed"
MAPPED_NOTE = ("orders.csv: the header holds the contract's sample headers; columns read by them: "
               "'Order ID' -> order_id, 'Order Date' -> order_date, 'Net Sales' -> net_sales; raw/orders.csv has the "
               "contract's names in the contract's order")
BOTH_FORMS = ("land a CSV whose header line is exactly: order_id, order_date, net_sales (the contract's order); or "
              "one that holds each sample header once, in any order: 'Order ID', 'Order Date', 'Net Sales'; or "
              "propose the contract again")
V6_FIX = "land a CSV whose header is exactly: order_id, order_date, net_sales; or propose the contract again"
V6_TABLE_HEAD = ("| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | "
                 "Description |")


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ if env is None else env)
    e["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=e, timeout=300)


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return path


def agreed(base: Path, *, header: list[str] = HEADER, columns: str | None = None, total: str = "net_sales=60.5") -> Path:
    """A model folder with an agreed contract for 'orders', proposed from base/sample/orders.csv (or with --columns),
    agreed on the task-file channel."""
    root = base / "m"
    sample = write_csv(base / "sample" / "orders.csv", header, ROWS)
    args = ["contract", "propose", str(root), "--table", TABLE, "--unit", "net_sales=KRW", "--total", total]
    args += ["--columns", columns, "--key", "order_id"] if columns else ["--csv", str(sample)]
    r = run_cli(*args)
    assert r.returncode == 0, r.stdout + r.stderr
    task = base / "task.md"
    task.write_text(REPLY + ".\n", encoding="utf-8")
    r = run_cli("contract", "agree", str(root), "--table", TABLE, "--reply", REPLY, "--by", BY,
                "--channel", "task-file", "--source-file", str(task))
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def land_cli(root: Path, csv_path: Path, *extra: str) -> subprocess.CompletedProcess:
    return run_cli("land", str(root), "--table", TABLE, "--csv", str(csv_path), *extra)


def parsed(path: Path, names: list[str] = NAMES) -> tuple[list[str], list[tuple]]:
    """(header, rows typed by the contract types in the order of `names`) of a CSV file."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    header = [h.strip() for h in rows[0]]
    return header, [tuple(land.parse_value(r[i], TYPES[NAMES.index(n)])[0] for i, n in enumerate(names))
                    for r in rows[1:]]


def raw_bytes(root: Path) -> dict[str, bytes]:
    raw = root / "raw"
    return {p.name: p.read_bytes() for p in sorted(raw.iterdir())} if raw.is_dir() else {}


def errors(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("ERR")]


def minimal_spec(root: Path) -> None:
    common.write_json(root / "model.spec.json", {
        "rotm_spec": 1, "name": "orders", "title": "Orders", "sheets": [
            {"name": "raw_orders", "type": "raw", "table": TABLE},
            {"name": "h_orders", "type": "helpers", "table": TABLE},
            {"name": "calc", "type": "calc", "blocks": [
                {"id": "tot", "title": "Net sales", "unit": "KRW", "rows": {"values": ["all"]},
                 "cell": "SUM({h:orders.net_sales})", "key": True}]},
            {"name": "checks", "type": "checks", "checks": [
                {"id": "tot_ties", "kind": "equal", "label": "Net sales tie to the raw sheet", "actual": "{cell:tot}",
                 "expected": "SUM({raw:orders.net_sales})"}]}]})


# ============================================================================= [C-sol-8]

ORDERS = {"as_sampled": (0, 1, 2), "other_order": (2, 0, 1)}


@pytest.mark.parametrize("order", list(ORDERS.values()), ids=list(ORDERS))
def test_csv_with_spaced_headers_lands_by_its_sample_headers(outside_tmp: Path, order: tuple[int, ...]) -> None:
    root = agreed(outside_tmp)
    data = common.read_json(modelfolder.contract_path(root, TABLE))
    assert [c["name"] for c in data["columns"]] == NAMES
    assert [c["csv_header"] for c in data["columns"]] == HEADER
    md = contract.md_path(root, TABLE).read_text(encoding="utf-8")
    assert "| # | Column | CSV header | Type |" in md and "| 1 | order_id | Order ID | text |" in md
    pairs = "'Order ID' -> order_id, 'Order Date' -> order_date, 'Net Sales' -> net_sales"
    assert (f"- land --csv accepts a CSV whose header line holds these sample headers, each once, in any order: "
            f"{pairs}; or the destination names in the contract's order; land writes raw/orders.csv with the "
            "destination names") in md
    src = outside_tmp / "sample" / "orders.csv"
    if order != (0, 1, 2):
        src = write_csv(outside_tmp / "export" / "orders.csv", [HEADER[i] for i in order],
                        [[r[i] for i in order] for r in ROWS])
    r = land_cli(root, src)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert MAPPED_NOTE in out
    snap = root / "raw" / "orders.csv"
    header, rows = parsed(snap)
    assert header == NAMES
    _h, file_rows = parsed(src, [NAMES[i] for i in order])
    assert rows == [tuple(r[[NAMES[i] for i in order].index(n)] for n in NAMES) for r in file_rows]
    assert rows == [("A1", land.parse_value("2026-01-05", "date")[0], 10.5),
                    ("A2", land.parse_value("2026-01-06", "date")[0], 20.0),
                    ("A3", land.parse_value("2026-01-07", "date")[0], 30.0)]
    # the same rule with --force and --control (the source total is recorded for the snapshot)
    r = land_cli(root, src, "--force", "--control", "orders.net_sales=60.5")
    assert r.returncode == 0 and MAPPED_NOTE in r.stdout, r.stdout + r.stderr
    assert parsed(snap)[0] == NAMES
    minimal_spec(root)
    b = run_cli("build", str(root), "--strict", "--force")
    assert b.returncode == 0, b.stdout + b.stderr
    c = run_cli("check", str(root), "--strict", "--no-excel")
    assert c.returncode == 3, c.stdout + c.stderr
    assert "errors: 0  warnings: 0" in c.stdout, c.stdout


BAD = {
    "missing": (HEADER[:2], [r[:2] for r in ROWS], "missing 'Net Sales'"),
    "extra": (HEADER + ["Region"], [r + ["East"] for r in ROWS], "not in the contract 'Region'"),
    "repeated": (HEADER + ["Net Sales"], [r + [r[2]] for r in ROWS], "given twice: 'Net Sales'"),
}


@pytest.mark.parametrize("case", list(BAD), ids=list(BAD))
def test_sample_header_landing_refuses_a_missing_extra_or_repeated_header(outside_tmp: Path, case: str) -> None:
    root = agreed(outside_tmp)
    r = land_cli(root, outside_tmp / "sample" / "orders.csv")
    assert r.returncode == 0, r.stdout + r.stderr
    before = raw_bytes(root)
    assert "orders.csv" in before
    header, rows, words = BAD[case]
    bad = write_csv(outside_tmp / "bad" / "orders.csv", header, rows)
    for extra in ((), ("--force",), ("--force", "--control", "orders.net_sales=60.5")):
        r = land_cli(root, bad, *extra)
        out = r.stdout + r.stderr
        assert r.returncode == 1, (extra, out)
        errs = errors(out)
        assert len(errs) == 1 and "L-COLUMNS" in errs[0], (extra, out)
        assert words in errs[0] and BOTH_FORMS in errs[0], errs[0]
        assert "nothing was written" in out
        assert raw_bytes(root) == before, extra


def test_plain_headers_land_as_before(outside_tmp: Path) -> None:
    base_new, base_old = outside_tmp / "new", outside_tmp / "old"
    new = agreed(base_new, header=NAMES)
    data = common.read_json(modelfolder.contract_path(new, TABLE))
    assert [c["csv_header"] for c in data["columns"]] == NAMES
    md = contract.md_path(new, TABLE).read_text(encoding="utf-8")
    assert V6_TABLE_HEAD in md and "CSV header" not in md and "land --csv accepts" not in md
    stripped = json.loads(json.dumps(data))
    for c in stripped["columns"]:
        del c["csv_header"]
    # the .md is as in v6 (the status line differs: removing the key changes the hash the agreement holds)
    assert contract._shown_text(contract.render_md(stripped)) == contract._shown_text(contract.render_md(data))
    old = agreed(base_old, columns="order_id,order_date:date,net_sales:number")
    assert all("csv_header" not in c for c in common.read_json(modelfolder.contract_path(old, TABLE))["columns"])
    src = base_new / "sample" / "orders.csv"
    for root in (new, old):
        r = land_cli(root, src)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "sample headers" not in r.stdout
    assert (new / "raw" / "orders.csv").read_bytes() == (old / "raw" / "orders.csv").read_bytes()
    assert parsed(new / "raw" / "orders.csv")[0] == NAMES


def test_contract_without_csv_header_refuses_a_differing_header(outside_tmp: Path) -> None:
    root = agreed(outside_tmp, columns="order_id,order_date:date,net_sales:number")
    data = common.read_json(modelfolder.contract_path(root, TABLE))
    assert all("csv_header" not in c for c in data["columns"])
    src = write_csv(outside_tmp / "export" / "orders.csv", HEADER, ROWS)
    r = land_cli(root, src)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    errs = errors(out)
    assert len(errs) == 1 and "L-COLUMNS" in errs[0] and V6_FIX in errs[0], out
    assert "missing order_id, order_date, net_sales" in errs[0]
    assert "sample header" not in out
    assert not (root / "raw" / "orders.csv").exists()


def _shipped() -> list[Path]:
    roots = [SKILL / "examples", SKILL / "templates"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        roots.append(PLUGIN / "templates")
    out: list[Path] = []
    for r in roots:
        if r.is_dir():
            out += sorted(p for p in r.iterdir() if (p / "model.spec.json").is_file())
    return out


SHIPPED = _shipped()


@pytest.mark.parametrize("folder", SHIPPED, ids=[f"{p.parent.name}/{p.name}" for p in SHIPPED])
def test_shipped_contracts_keep_their_hash_md_and_snapshot(folder: Path, tmp_path: Path) -> None:
    dest = tmp_path / folder.name
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "reports"))
    env = dict(os.environ, ROTM_EXAMPLE_ROOTS=str(tmp_path), PYTHONUTF8="1")
    tables = modelfolder.tables(folder)
    assert tables
    for t in tables:
        data = common.read_json(modelfolder.contract_path(folder, t))
        assert all("csv_header" not in c for c in data["columns"]), t
        assert modelfolder.contract_hash(data) == data["agreement"]["contract_sha256"], t
        shipped_md = contract.md_path(folder, t).read_text(encoding="utf-8-sig")
        assert contract._shown_text(contract.render_md(data)) == contract._shown_text(shipped_md), t
        (dest / "raw" / f"{t}.csv").unlink()
        r = run_cli("land", str(dest), "--table", t, "--csv", str(dest / "data" / f"{t}.csv"), env=env)
        assert r.returncode == 0, (t, r.stdout[-2000:], r.stderr[-2000:])
        assert (dest / "raw" / f"{t}.csv").read_bytes() == (folder / "raw" / f"{t}.csv").read_bytes(), t
    # the copy builds in strict mode, and its raw sheets hold the rows the shipped manifest records
    r = run_cli("build", str(dest), "--strict", "--force", "--quiet", env=env)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    from lib import manifest
    shipped = {e["table"]: e["digest"] for e in manifest.load_manifest(folder / "model.xlsx")["sheets"]
               if e.get("type") == "raw"}
    built = {e["table"]: e["digest"] for e in manifest.load_manifest(dest / "model.xlsx")["sheets"]
             if e.get("type") == "raw"}
    assert built == shipped


# ============================================================================= [A-aera-6]

def _md_numbers(md: str) -> tuple[float, float]:
    m = re.search(r"- Control total: sum of net_sales = (\S+) \(tolerance (\S+)\)", md)
    assert m, md
    return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))


def test_contract_md_shows_a_small_total_and_tolerance_as_stored(outside_tmp: Path) -> None:
    root = outside_tmp / "m"
    r = run_cli("contract", "propose", str(root), "--table", TABLE, "--columns",
                "order_id,order_date:date,net_sales:number", "--key", "order_id", "--unit", "net_sales=KRW",
                "--total", "net_sales=0.00000042:0.00000001")
    assert r.returncode == 0, r.stdout + r.stderr
    data = common.read_json(modelfolder.contract_path(root, TABLE))
    tot = data["expect"]["totals"][0]
    md = contract.md_path(root, TABLE).read_text(encoding="utf-8")
    assert "- Control total: sum of net_sales = 4.2e-07 (tolerance 1e-08)" in md, md
    assert _md_numbers(md) == (tot["value"], tot["tolerance"]) == (0.00000042, 0.00000001)


@pytest.mark.parametrize("value, text", [
    (1234567.5, "1,234,567.5"), (100, "100"), (100.0, "100"), (1234567, "1,234,567"), (0, "0"), (0.5, "0.5"),
    (0.01, "0.01"), (-1234.25, "-1,234.25"), (0.1234567, "0.1234567"), (0.00001, "0.00001"),
    (0.000001, "0.000001"), (0.00000042, "4.2e-07"), (-0.00000001, "-1e-08"), (12345.678901234, "12,345.678901234"),
    (0.30000000000000004, "0.30000000000000004"), (1e16, "10,000,000,000,000,000"),
])
def test_num_gives_the_shortest_text_of_the_stored_number(value: float, text: str) -> None:
    assert contract._num(value) == text
    assert float(contract._num(value).replace(",", "")) == value


def test_sample_profile_numbers_read_back(outside_tmp: Path) -> None:
    root = outside_tmp / "m"
    sample = write_csv(outside_tmp / "s.csv", ["id", "rate"],
                       [["a", "0.1234567"], ["b", "0.0000002"], ["c", "0.30000000000000004"]])
    r = run_cli("contract", "propose", str(root), "--table", "rates", "--csv", str(sample), "--unit", "rate=ratio")
    assert r.returncode == 0, r.stdout + r.stderr
    data = common.read_json(modelfolder.contract_path(root, "rates"))
    obs = data["columns"][1]["observed"]
    assert obs["max"] == 0.30000000000000004                   # 15 digits ('0.3') would not read back
    md = contract.md_path(root, "rates").read_text(encoding="utf-8")
    row = next(ln for ln in md.splitlines() if ln.startswith("| rate | 3 |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert [float(cells[i].replace(",", "")) for i in (4, 5, 6)] == [obs["min"], obs["max"], obs["sum"]], row


# ============================================================================= [C-sol-8]: versions and documents

def test_a_changed_sample_header_is_listed_under_changes() -> None:
    old = {"grain": "g", "key": ["order_id"], "columns": [{"name": "order_id", "type": "text", "null_rule": "never"},
                                                         {"name": "net_sales", "type": "number", "null_rule": "never"}]}
    new = json.loads(json.dumps(old))
    new["columns"][0]["csv_header"] = "order_id"          # the name itself: no change
    assert contract.changes_between(old, new) == []
    new["columns"][1]["csv_header"] = "Net Sales"
    assert contract.changes_between(old, new) == ["column net_sales csv_header: none -> Net Sales"]
    agreed_content = contract._agreed_content(new)
    assert agreed_content["columns"][1]["csv_header"] == "Net Sales"


REFS = SKILL / "references"


def _flat(text: str) -> str:
    return " ".join(text.replace("`", "").split())


def test_references_state_the_header_lines_land_accepts() -> None:
    cc = _flat((REFS / "column-contract.md").read_text(encoding="utf-8"))
    assert "## Sample headers and the header line land accepts" in cc
    c = {"table": "t", "columns": [{"name": "a_b", "csv_header": "A B"}, {"name": "c", "csv_header": "C"}]}
    fix = land._columns_fix(c, ["a_b", "c"])
    assert fix == ("land a CSV whose header line is exactly: a_b, c (the contract's order); or one that holds each "
                   "sample header once, in any order: 'A B', 'C'; or propose the contract again")
    assert ("land a CSV whose header line is exactly: <names> (the contract's order); or one that holds each sample "
            "header once, in any order: '<h1>', '<h2>', ...; or propose the contract again") in cc
    assert "land a CSV whose header is exactly: <names>; or propose the contract again" in cc
    assert ("<file name>: the header holds the contract's sample headers; columns read by them: '<h1>' -> <name1>, "
            "'<h2>' -> <name2>, ...; raw/<t>.csv has the contract's names in the contract's order") in cc
    assert "given twice: '<h>'" in cc
    msf = _flat((REFS / "model-spec-format.md").read_text(encoding="utf-8"))
    assert ("--csv: the CSV's header line is the contract's column names in the contract's order; or, for a contract "
            "proposed from a sample CSV (each column records its sample header as csv_header), the sample headers, "
            "each exactly once, in any order, compared after trimming spaces, and no other column.") in msf
    assert "| landing a CSV with the contract's names in another order |" in msf
    assert ("| landing a CSV whose header is not the contract's names | lands when the header holds the sample headers "
            "the contract recorded (csv_header), each once; otherwise L-COLUMNS, nothing written | land the file whose "
            "header was the sample's, or write the contract's names into the header line of a copy |") in msf


def test_model_spec_format_names_the_systems_land_writes_enable_folding_for() -> None:
    msf = _flat((REFS / "model-spec-format.md").read_text(encoding="utf-8"))
    m = re.search(r"with \[EnableFolding = true\] for (.*?), and without it for (.*?) \(", msf)
    assert m, "model-spec-format.md: no sentence 'with [EnableFolding = true] for ..., and without it for ...'"
    with_text, without_text = m.group(1), m.group(2)
    base = common.read_json(SKILL / "templates" / "starter" / "contracts" / "lines.contract.json")
    for name in ("Google BigQuery", "SQL Server", "Azure SQL", "Synapse", "PostgreSQL", "Snowflake", "Amazon Redshift",
                 "SAP HANA", "Oracle", "MySQL", "Teradata"):
        c = dict(base, source=dict(base.get("source") or {}, system=name))
        folding = "[EnableFolding = true]" in land.m_text(c)
        assert folding == (name in with_text), (name, with_text)
        assert (not folding) == (name in without_text or name == "Teradata"), (name, without_text)
    assert "a system land does not know" in without_text

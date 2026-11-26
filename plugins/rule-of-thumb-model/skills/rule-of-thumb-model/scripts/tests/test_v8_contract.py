"""Tests of the v8 change of package P5 to the contract (round 7 verdict, [A-aera-4] and [A-aera-8, C-sol-2]).

[A-aera-4]: a contract in which one column's sample header (csv_header) is another column's destination name is
refused by contract show (no .md written), contract agree (exit 2, nothing recorded) and land (nothing written, also
with --force and --control, and without --csv), because a header line could be read by the names or by the sample
headers with other columns. contract propose never writes such a contract: a derived name that would be another
column's sample header becomes <name>_<k> (the first free k from 1), and a note under "Points to confirm" says so.

[A-aera-8, C-sol-2]: contract propose reads a sample as land reads it, counts the records of only delimiters and
names their lines under "Points to confirm"; the sample profile leaves them out of its statistics.

Model folders lie outside the plugin (fixture outside_tmp); the commands run with ROTM_NO_EXCEL=1.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, land, modelfolder

SCRIPTS = Path(__file__).resolve().parents[1]
BY = "Owner"
TABLE = "ledger"
REPLY = "The ledger contract is agreed"
ROWS = [["E1", "10", "80"], ["E2", "25", "5"], ["E3", "7", "0"]]
SWAP_MESSAGE = ("contract 'ledger': column 'credit' has the sample header 'debit', which is the destination name of "
                "column 'debit'; column 'debit' has the sample header 'credit', which is the destination name of "
                "column 'credit', so a header line could be read by either; {done}")
SWAP_FIX = ("rename the destination columns 'debit' and 'credit', or remove the csv_header entries (contract propose "
            "--force with --columns), then run contract show and get the contract agreed again")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    e = dict(os.environ)
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


def propose(root: Path, sample: Path, *extra: str) -> subprocess.CompletedProcess:
    return run_cli("contract", "propose", str(root), "--table", TABLE, "--csv", str(sample), *extra)


def agree(base: Path, root: Path) -> subprocess.CompletedProcess:
    task = base / "task.md"
    task.write_text(REPLY + ".\n", encoding="utf-8")
    return run_cli("contract", "agree", str(root), "--table", TABLE, "--reply", REPLY, "--by", BY,
                   "--channel", "task-file", "--source-file", str(task))


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and "reports" not in p.relative_to(root).parts}


def errors(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("ERR")]


# ============================================================================= [A-aera-4]

def _swapped(base: Path) -> Path:
    """A contract proposed from the sample id,debit,credit whose destination names debit and credit were then
    swapped in the JSON, so that column 'credit' has the sample header 'debit' and column 'debit' the header
    'credit'."""
    root = base / "m"
    sample = write_csv(base / "sample" / "ledger.csv", ["id", "debit", "credit"], ROWS)
    r = propose(root, sample, "--key", "id", "--unit", "debit=KRW", "--unit", "credit=KRW")
    assert r.returncode == 0, r.stdout + r.stderr
    path = modelfolder.contract_path(root, TABLE)
    data = common.read_json(path)
    assert [(c["name"], c["csv_header"]) for c in data["columns"]] == [("id", "id"), ("debit", "debit"),
                                                                      ("credit", "credit")]
    data["columns"][1]["name"], data["columns"][2]["name"] = "credit", "debit"
    common.write_json(path, data)
    return root


def _agreed_by_hand(root: Path) -> None:
    """The agreement record a contract agreed before v8 holds (written here, since v8's agree refuses it)."""
    path = modelfolder.contract_path(root, TABLE)
    data = common.read_json(path)
    h = modelfolder.contract_hash(data)
    data["agreement"] = {"status": "agreed", "contract_sha256": h, "table": TABLE, "reply": REPLY, "by": BY,
                         "at": "2026-11-26T09:00:00+09:00", "channel": "session", "source_file": None,
                         "recorded_by": "rotm.py contract agree", "version": int(data.get("version") or 1),
                         "shown_sha256": h}
    common.write_json(path, data)
    contract.write_md(root, data)
    assert modelfolder.contract_state(root, TABLE) == "agreed"


def test_swapped_sample_headers_are_refused(outside_tmp: Path) -> None:
    root = _swapped(outside_tmp)
    assert contract.header_name_pairs(common.read_json(modelfolder.contract_path(root, TABLE))) == [
        ("credit", "debit", "debit"), ("debit", "credit", "credit")]
    before = files(root)
    r = run_cli("contract", "show", str(root), "--table", TABLE)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert SWAP_MESSAGE.format(done="nothing was recorded") in out and SWAP_FIX in out, out
    assert files(root) == before                                     # no .md written, no hash recorded
    r = agree(outside_tmp, root)
    out = r.stdout + r.stderr
    assert r.returncode == 2 and SWAP_MESSAGE.format(done="nothing was recorded") in out, out
    assert files(root) == before                                     # nothing recorded
    assert modelfolder.contract_state(root, TABLE) == "proposed"
    # a contract agreed before v8 with such a pair: land writes nothing, for the header in either order
    _agreed_by_hand(root)
    before = files(root)
    for header in (["id", "debit", "credit"], ["id", "credit", "debit"]):
        extract = write_csv(outside_tmp / "export" / "ledger.csv", header, ROWS)
        for flags in ((), ("--force",), ("--force", "--control", "ledger.debit=42")):
            r = run_cli("land", str(root), "--table", TABLE, "--csv", str(extract), *flags)
            out = r.stdout + r.stderr
            assert r.returncode == 2, (header, flags, out)
            assert SWAP_MESSAGE.format(done="nothing was written") in out and SWAP_FIX in out, out
            assert files(root) == before, (header, flags)
    r = run_cli("land", str(root), "--table", TABLE)                 # the query texts only
    assert r.returncode == 2 and SWAP_MESSAGE.format(done="nothing was written") in r.stdout + r.stderr
    assert files(root) == before


def test_one_sample_header_that_is_another_columns_name_is_refused_with_the_verdict_text(outside_tmp: Path) -> None:
    root = outside_tmp / "m"
    sample = write_csv(outside_tmp / "sample" / "ledger.csv", ["id", "gross", "Gross Amount"], ROWS)
    assert propose(root, sample, "--key", "id").returncode == 0
    path = modelfolder.contract_path(root, TABLE)
    data = common.read_json(path)
    assert [(c["name"], c["csv_header"]) for c in data["columns"]] == [("id", "id"), ("gross", "gross"),
                                                                      ("gross_amount", "Gross Amount")]
    data["columns"][1]["name"], data["columns"][2]["name"] = "net", "gross"
    common.write_json(path, data)
    refusal = contract.header_name_refusal(data)
    assert refusal is not None
    assert refusal.message == ("contract 'ledger': column 'net' has the sample header 'gross', which is the "
                               "destination name of column 'gross', so a header line could be read by either; "
                               "nothing was recorded")
    assert refusal.fix == ("rename the destination column 'gross', or remove the csv_header entries (contract "
                           "propose --force with --columns), then run contract show and get the contract agreed again")
    r = run_cli("contract", "show", str(root), "--table", TABLE)
    assert r.returncode == 2 and refusal.message in r.stdout + r.stderr
    # renaming the destination column ends the refusal
    data["columns"][2]["name"] = "gross_total"
    common.write_json(path, data)
    assert contract.header_name_pairs(data) == []
    assert run_cli("contract", "show", str(root), "--table", TABLE).returncode == 0
    assert agree(outside_tmp, root).returncode == 0


def test_distinct_sample_headers_land_by_either_header_line(outside_tmp: Path) -> None:
    root = outside_tmp / "m"
    header = ["Entry ID", "Debit Amount", "Credit Amount"]
    sample = write_csv(outside_tmp / "sample" / "ledger.csv", header, ROWS)
    assert propose(root, sample, "--key", "entry_id", "--unit", "debit_amount=KRW",
                   "--unit", "credit_amount=KRW").returncode == 0
    data = common.read_json(modelfolder.contract_path(root, TABLE))
    assert [c["name"] for c in data["columns"]] == ["entry_id", "debit_amount", "credit_amount"]
    assert contract.header_name_pairs(data) == []
    assert agree(outside_tmp, root).returncode == 0
    names = write_csv(outside_tmp / "names" / "ledger.csv", ["entry_id", "debit_amount", "credit_amount"], ROWS)
    r = run_cli("land", str(root), "--table", TABLE, "--csv", str(names))
    assert r.returncode == 0, r.stdout + r.stderr
    snap = (root / "raw" / "ledger.csv").read_bytes()
    assert snap == b"entry_id,debit_amount,credit_amount\nE1,10,80\nE2,25,5\nE3,7,0\n"
    order = (2, 0, 1)
    moved = write_csv(outside_tmp / "moved" / "ledger.csv", [header[i] for i in order],
                      [[r[i] for i in order] for r in ROWS])
    r = run_cli("land", str(root), "--table", TABLE, "--csv", str(moved), "--force")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "raw/ledger.csv already holds these 3 rows (same digest); nothing changed" in out, out
    assert ("'Entry ID' -> entry_id, 'Debit Amount' -> debit_amount, 'Credit Amount' -> credit_amount") in out
    assert (root / "raw" / "ledger.csv").read_bytes() == snap


HEADERS = {
    "space_and_underscore": (["a b", "a_b"], ["a_b_1", "a_b_2"], 0, "a_b"),
    "the_same_header_twice": (["amount", "amount"], ["amount_1", "amount_2"], 0, "amount"),
    "a_free_name_taken": (["x y", "x_y", "x_y_1"], ["x_y_3", "x_y_2", "x_y_1"], 0, "x_y"),
}


@pytest.mark.parametrize("case", list(HEADERS), ids=list(HEADERS))
def test_propose_never_writes_a_header_that_is_another_columns_name(outside_tmp: Path, case: str) -> None:
    header, names, moved, old = HEADERS[case]
    root = outside_tmp / "m"
    rows = [[f"r{i}"] + [str(i * 10 + j) for j in range(1, len(header))] for i in range(1, 4)]
    sample = write_csv(outside_tmp / "sample" / "ledger.csv", header, rows)
    r = propose(root, sample)
    assert r.returncode == 0, r.stdout + r.stderr
    data = common.read_json(modelfolder.contract_path(root, TABLE))
    assert [(c["name"], c["csv_header"]) for c in data["columns"]] == list(zip(names, header)), data["columns"]
    assert contract.header_name_pairs(data) == []
    clash = next(j for j, h in enumerate(header) if j != moved and h == old)
    note = (f"header {moved + 1} ('{header[moved]}'): named '{names[moved]}', not '{old}', because '{old}' is the "
            f"sample header of column {clash + 1}; a destination name that is another column's sample header would "
            "let land read a header line either way")
    assert note in data["notes"], data["notes"]
    md = contract.md_path(root, TABLE).read_text(encoding="utf-8")
    assert md.index("Points to confirm:") < md.index(note)
    assert run_cli("contract", "show", str(root), "--table", TABLE).returncode == 0
    r = agree(outside_tmp, root)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("land", str(root), "--table", TABLE, "--csv", str(sample))
    if len(set(header)) == len(header):                      # read by the sample headers
        assert r.returncode == 0, r.stdout + r.stderr
    else:                                                     # a header given twice: only the names are accepted
        assert r.returncode == 1 and "L-COLUMNS" in r.stdout, r.stdout + r.stderr
        named = write_csv(outside_tmp / "named" / "ledger.csv", [c["name"] for c in data["columns"]], rows)
        r = run_cli("land", str(root), "--table", TABLE, "--csv", str(named))
        assert r.returncode == 0, r.stdout + r.stderr


PLAIN = {
    "cleaned_and_repeated": (["Invoice ID", "Amount (KRW)", "Amount (KRW)"], ["invoice_id", "amount_krw", "amount_krw_2"]),
    "spaced": (["Order ID", "Order Date", "Net Sales"], ["order_id", "order_date", "net_sales"]),
    "case_only": (["Amount", "amount"], ["Amount", "amount_2"]),
    "plain": (["id", "debit", "credit"], ["id", "debit", "credit"]),
}


@pytest.mark.parametrize("case", list(PLAIN), ids=list(PLAIN))
def test_plain_and_cleaned_headers_keep_their_names(tmp_path: Path, case: str) -> None:
    """Headers whose derived names are no other column's sample header keep the v7 names (nothing renamed)."""
    header, names = PLAIN[case]
    sample = write_csv(tmp_path / "s.csv", header, [[f"r{i}"] + ["1"] * (len(header) - 1) for i in range(3)])
    prof = contract.profile_csv(sample)
    assert prof["names"] == names
    assert not any("because" in n and "sample header of column" in n for n in prof["notes"]), prof["notes"]


# ============================================================================= [A-aera-8, C-sol-2]

def test_sample_profile_names_records_of_only_delimiters(outside_tmp: Path) -> None:
    text = ("id,region,amount\n"
            "A1,East,10\n"
            ",,\n"                 # line 3
            "A2,West,20\n"
            "\n"                   # line 5: blank, skipped
            ",,\n"                 # line 6
            "A3,East,30\n")
    sample = outside_tmp / "sample" / "ledger.csv"
    sample.parent.mkdir(parents=True)
    sample.write_text(text, encoding="utf-8")
    prof = contract.profile_csv(sample)
    assert prof["only_delimiters_lines"] == [3, 6]
    assert prof["rows"] == 3 and prof["lines"] == [2, 4, 7]
    assert [c["nulls"] for c in prof["columns"]] == [0, 0, 0]          # the statistics leave them out
    root = outside_tmp / "m"
    r = propose(root, sample, "--key", "id", "--unit", "amount=KRW")
    assert r.returncode == 0, r.stdout + r.stderr
    data = common.read_json(modelfolder.contract_path(root, TABLE))
    note = ("2 record(s) hold only delimiters (every field empty): lines 3, 6; land refuses them, because the first "
            "column, which is never null, is empty in them; remove them from the extract")
    assert note in data["notes"], data["notes"]
    assert data["description"] == "proposed from the sample ledger.csv (3 rows, 3 columns)"
    assert all(c["null_rule"] == "never" for c in data["columns"])
    md = contract.md_path(root, TABLE).read_text(encoding="utf-8")
    assert md.index("Points to confirm:") < md.index(note)
    # land refuses the same sample, naming the same lines
    assert agree(outside_tmp, root).returncode == 0
    r = run_cli("land", str(root), "--table", TABLE, "--csv", str(sample))
    assert r.returncode == 1, r.stdout + r.stderr
    assert all("(lines 3, 6 hold only delimiters: every field is empty)" in e for e in errors(r.stdout)
               if "L-NULL" in e), r.stdout
    assert not (root / "raw" / "ledger.csv").exists()
    one = outside_tmp / "one" / "ledger.csv"
    one.parent.mkdir(parents=True)
    one.write_text("id,amount\nA1,1\n,\n", encoding="utf-8")
    assert ("1 record(s) hold only delimiters (every field empty): line 3; land refuses them, because the first "
            "column, which is never null, is empty in them; remove them from the extract") in contract.profile_csv(
        one)["notes"]


def test_propose_notes_name_physical_lines(outside_tmp: Path) -> None:
    """The lines propose names for a repeated key and for empty cells are the lines land names (v8)."""
    rows = "".join(f"K{i},East,{i}\n" for i in range(1, 120))
    text = "id,region,amount\n\n" + rows + "K5,West,7\nK200,,8\n"
    sample = outside_tmp / "sample" / "ledger.csv"
    sample.parent.mkdir(parents=True)
    sample.write_text(text, encoding="utf-8")
    root = outside_tmp / "m"
    r = propose(root, sample, "--key", "id", "--unit", "amount=KRW")
    assert r.returncode == 0, r.stdout + r.stderr
    data = common.read_json(modelfolder.contract_path(root, TABLE))
    notes = " ".join(data["notes"])
    assert "lines 7 and 122: K5" in notes, notes                 # K5 is on line 7 (line 2 is blank)
    assert "(line 123)" in notes, notes                          # the empty region
    assert agree(outside_tmp, root).returncode == 0
    r = run_cli("land", str(root), "--table", TABLE, "--csv", str(sample))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "lines 7 and 122: K5" in r.stdout and "lines 123" in r.stdout, r.stdout


def test_land_reader_keeps_records_of_only_delimiters(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text('a,b\n,\n\n""\n1,2\n', encoding="utf-8")
    header, body, lines = land._read_csv(p, "utf-8-sig", ",")
    assert header == ["a", "b"] and body == [["", ""], [""], ["1", "2"]] and lines == [2, 4, 5]
    assert land.is_only_delimiters(["", ""]) and land.is_only_delimiters([""])
    assert not land.is_only_delimiters([]) and not land.is_only_delimiters(["", " "])

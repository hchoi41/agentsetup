"""Tests of the v9 entry [A-aera-2, C-sol-1] (package P2 of the v9 plan): a control total accepted for a snapshot is
used only under the agreement it was accepted under. Owner: P2.

- raw/<t>.controls.json entries record agreement.contract_sha256 of the agreed contract in force (land --control and
  check --accept-refresh --control).
- controls.lookup: an entry applies when its hash equals the agreed contract's; an entry without the hash (written
  before v9) applies only when its time is not earlier than the agreement's, and not when either time is missing.
- An entry for the snapshot that does not apply is not used: land, build, check and lift use the contract's total and
  print one note naming the entry; L-TOTAL (land) and D-TOTAL (check) name both totals and the way on.
- The same values again under the same agreement keep the entry as it is; a record under a new agreement replaces it.

The names of the first seven tests are fixed by the v9 plan (section 5.3) and quoted by the documents. Tests marked
excel drive Excel through lib.excel. The contracts are agreed through the task-file channel (the starter copies) or
as test_build.agree writes them (the starter of test_v4_check).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import check, common, contract, controls, land, lift, manifest, modelfolder
import test_check as tc

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PY = [sys.executable, "-P", "-X", "utf8", "-B"]
ROTM_CMD = PY + [str(SCRIPTS / "rotm.py")]
REPLY = "agreed, land it as proposed."
REPLY_NEW = "agreed: the new control total of amount is the one to check."
TRUE_SUM = 57514500                     # the sum of amount in the starter's data/lines.csv (its contract's total)
TYPES = ["text", "date", "text", "number"]
EARLIER = "was accepted under an earlier agreement"


# ----------------------------------------------------------------------------- helpers

def _rotm(*args: str, excel_on: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if excel_on:
        env.pop("ROTM_NO_EXCEL", None)
    else:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run(ROTM_CMD + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=900)


def _out(r: subprocess.CompletedProcess[str]) -> str:
    return ((r.stdout or "") + (r.stderr or ""))[-6000:]


def _ok(r: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
    assert r.returncode == 0, _out(r)
    return r


def _task(folder: Path, name: str, reply: str) -> Path:
    path = folder / f"{name}.md"
    path.write_text(f"# Task\n\nContract reply from the user: {reply}\n", encoding="utf-8")
    return path


def _agreed(root: Path, table: str = "lines") -> dict:
    return common.read_json(root / "contracts" / f"{table}.contract.json")


def _hash(root: Path, table: str = "lines") -> str:
    return _agreed(root, table)["agreement"]["contract_sha256"]


def _starter(tmp_path: Path) -> Path:
    """A copy of the starter template (rotm.py init --template starter), its contract shown and agreed through the
    task-file channel, data/lines.csv landed."""
    root = tmp_path / "model"
    task = _task(tmp_path, "task", REPLY)
    for args in (("init", str(root), "--template", "starter"),
                 ("contract", "show", str(root), "--table", "lines"),
                 ("contract", "agree", str(root), "--table", "lines", "--reply", REPLY, "--by", "Max",
                  "--channel", "task-file", "--source-file", str(task)),
                 ("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"))):
        _ok(_rotm(*args))
    return root


def _new_agreement(root: Path, total: float, tmp_path: Path, name: str = "task_new") -> str:
    """The contract proposed again (--force) with the same columns and another control total of amount, shown and
    agreed through the task-file channel; returns the new agreement.contract_sha256."""
    old = _hash(root)
    _ok(_rotm("contract", "propose", str(root), "--table", "lines", "--force", "--csv", str(root / "raw" / "lines.csv"),
              "--key", "line_id", "--type", "amount=number", "--unit", "amount=KRW", "--total",
              f"amount={int(total) if float(total).is_integer() else total}"))
    _ok(_rotm("contract", "show", str(root), "--table", "lines"))
    task = _task(tmp_path, name, REPLY_NEW)
    _ok(_rotm("contract", "agree", str(root), "--table", "lines", "--reply", REPLY_NEW, "--by", "Max",
              "--channel", "task-file", "--source-file", str(task)))
    c = _agreed(root)
    assert [col["type"] for col in c["columns"]] == TYPES          # the snapshot digest is computed over these
    assert c["expect"]["totals"][0]["value"] == total
    new = c["agreement"]["contract_sha256"]
    assert new != old
    return new


def _three_files(root: Path, table: str = "lines") -> dict[str, bytes | None]:
    """raw/<t>.csv, raw/<t>.controls.json and the manifest, byte for byte (None when absent)."""
    paths = [root / "raw" / f"{table}.csv", root / "raw" / f"{table}{controls.SUFFIX}", root / "model.manifest.json"]
    return {p.name: (p.read_bytes() if p.is_file() else None) for p in paths}


def _report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _errors(report: dict, rule: str) -> list[dict]:
    return [f for f in report["findings"] if f["rule"] == rule and f["severity"] == "error"]


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def _export(root: Path, folder: Path, more: int = 3) -> tuple[Path, int]:
    """data/lines.csv with `more` rows added (another total); returns (file, its sum of amount)."""
    rows = _rows(root / "data" / "lines.csv")
    for i in range(more):
        rows.append({"line_id": f"L9{i:03d}", "line_date": "2026-03-15", "category": "Rent",
                     "amount": str(100000 * (i + 1))})
    return _write_rows(folder / "export_lines.csv", rows), sum(int(float(r["amount"])) for r in rows)


def _control_cell(ws) -> tuple[str, str]:
    """(value cell, source cell) of the control total of lines.amount on the inputs sheet."""
    for row in ws.iter_rows():
        for c in row:
            if c.value == "lines: control total of amount":
                return f"D{c.row}", f"E{c.row}"
    raise AssertionError("the inputs sheet has no control total of lines.amount")


def _inputs_control(root: Path) -> tuple[object, str]:
    import openpyxl
    ws = openpyxl.load_workbook(root / "model.xlsx")["inputs"]
    value, source = _control_cell(ws)
    return ws[value].value, str(ws[source].value or "")


def _check_links_the_control_cell(root: Path) -> None:
    """The expected side of the check lines_total_amount links the inputs cell _control_cell names."""
    import openpyxl
    man = manifest.load_manifest(root / "model.xlsx")
    sheet, cell = common.parse_ref(man["inputs"]["lines_total_amount"])
    ws = openpyxl.load_workbook(root / "model.xlsx")[sheet]
    assert _control_cell(ws)[0] == cell, (cell, _control_cell(ws))


# ============================================================================= fixed names (plan 5.3)

def test_land_control_record_is_not_used_after_a_new_agreement(tmp_path: Path) -> None:
    """A total recorded by land --control, then the contract proposed, shown and agreed again with another total:
    land of the same extract without --control reports L-TOTAL naming both totals, does not exit 0, and
    raw/lines.csv, raw/lines.controls.json and the manifest stay byte for byte. v8 exited 0 (the entry was found by
    the snapshot's digest alone)."""
    root = _starter(tmp_path)
    first = _hash(root)
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"),
              "--control", f"lines.amount={TRUE_SUM}"))
    entry = controls.load(root, "lines")["accepted"][0]
    assert entry["totals"][0]["value"] == TRUE_SUM
    other = TRUE_SUM + 1000
    _new_agreement(root, other, tmp_path)
    before = _three_files(root)
    out = tmp_path / "land.json"
    r = _rotm("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"), "--json", str(out))
    assert r.returncode != 0, _out(r)
    report = _report(out)
    f = _errors(report, "L-TOTAL")
    assert len(f) == 1, report["findings"]
    assert f[0]["message"] == (
        f"lines.csv: the sum of amount is 57,514,500; the contract's control total is 57,515,500 (tolerance 0; "
        f"difference -1,000); the total 57,514,500 accepted for these rows at {entry['at']} was accepted under an "
        "earlier agreement and is not used"), f[0]["message"]
    assert f[0]["fix"] == (
        f"give the source's current total: rotm.py land \"{root.as_posix()}\" --table lines --csv <file> --force "
        "--control lines.amount=<source total>; it is recorded for this snapshot under the agreement in force")
    notes = "\n".join(report["notes"])
    assert (f"raw/lines.controls.json: the total amount = 57,514,500 accepted for this snapshot at {entry['at']} was "
            "accepted under an earlier agreement of contract 'lines' and is not used; the agreed contract's total "
            "57,515,500 is used") in notes, notes
    assert _three_files(root) == before
    assert entry["contract_sha256"] == first                    # v9: the entry names the agreement it was made under


@pytest.mark.excel
def test_acceptance_record_is_not_used_after_a_new_agreement(tmp_path: Path) -> None:
    """The same with the record written by check --accept-refresh --control over a Power Query table (the starter
    with raw_lines landed by a generated query, refreshed with 7 rows more)."""
    from lib import build
    from test_v4_check import _pq_landing, _total, starter
    root = starter(tmp_path / "m", spec_edit=_pq_landing)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data.get("power_query_attached") == ["lines"], [f.line() for f in rep.findings]
    rows = _rows(root / "raw" / "lines.csv")
    more = rows + [dict(r, line_id=f"M{r['line_id'][1:]}") for r in rows[:7]]
    total = _total(more)
    _write_rows(root / "raw" / "lines.csv", more)              # the source changed; the query reads this file
    rep = check.check_model(root, refresh=True)
    assert "R-CHANGED" in [f.rule for f in rep.findings], [f.line() for f in rep.findings]
    assert check.extend_helpers(root, table="lines").exit_code == 0
    text = f"lines.amount={total}"
    rep = check.check_model(root, accept_refresh=True, controls={"lines.amount": total}, control_texts=[text])
    assert "R-ACCEPTED" in [f.rule for f in rep.findings], [f.line() for f in rep.findings] + rep.notes
    entry = controls.load(root, "lines")["accepted"][0]
    assert entry["contract_sha256"] == _hash(root) and entry["given"] == [text]
    extract = _write_rows(tmp_path / "extract" / "lines.csv", _rows(root / "raw" / "lines.csv"))
    _new_agreement(root, total + 1000, tmp_path)
    before = _three_files(root)
    out = tmp_path / "land.json"
    r = _rotm("land", str(root), "--table", "lines", "--csv", str(extract), "--json", str(out))
    assert r.returncode != 0, _out(r)
    f = _errors(_report(out), "L-TOTAL")
    assert len(f) == 1 and EARLIER in f[0]["message"], _out(r)
    assert land._fmt(total) in f[0]["message"] and land._fmt(total + 1000) in f[0]["message"]
    assert _three_files(root) == before


@pytest.mark.excel
def test_build_and_check_use_the_new_contract_total_after_a_new_agreement(tmp_path: Path) -> None:
    """After the new agreement, build --strict writes the new contract's total as the expected value of
    lines_total_amount (the inputs cell the check links), its source text names the total that is not used, and
    check --strict (Excel) does not exit 0: D-TOTAL names both totals."""
    root = _starter(tmp_path)
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"),
              "--control", f"lines.amount={TRUE_SUM}"))
    at = controls.load(root, "lines")["accepted"][0]["at"]
    other = TRUE_SUM + 1000
    _new_agreement(root, other, tmp_path)
    built = _rotm("build", str(root), "--strict", "--force", "--json", str(tmp_path / "build.json"), excel_on=True)
    assert (root / "model.xlsx").is_file(), _out(built)
    value, source = _inputs_control(root)
    assert value == other, (value, source)
    assert source == f"Source: contract lines, expect.totals (the total 57514500 accepted for this snapshot at {at} " \
                     "under an earlier agreement is not used)", source
    _check_links_the_control_cell(root)
    assert any(EARLIER in n for n in _report(tmp_path / "build.json")["notes"])
    out = tmp_path / "check.json"
    checked = _rotm("check", str(root), "--strict", "--json", str(out), excel_on=True)
    assert checked.returncode != 0, _out(checked)
    f = _errors(_report(out), "D-TOTAL")
    assert f and EARLIER in f[0]["message"] and "57,515,500" in f[0]["message"], _out(checked)
    assert "--control lines.amount=<source total>" in f[0]["fix"] and "under the agreement in force" in f[0]["fix"]


@pytest.mark.excel
def test_land_force_control_after_a_new_agreement_records_the_new_hash(tmp_path: Path) -> None:
    """land --force --control with the rows' true sum after the new agreement exits 0; the entry carries the new
    hash (one entry for the digest); build --strict and check --strict (Excel) exit 0."""
    root = _starter(tmp_path)
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"),
              "--control", f"lines.amount={TRUE_SUM}"))
    new = _new_agreement(root, TRUE_SUM + 1000, tmp_path)
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"), "--force",
              "--control", f"lines.amount={TRUE_SUM}"))
    data = controls.load(root, "lines")
    assert len(data["accepted"]) == 1 and data["accepted"][0]["contract_sha256"] == new
    _ok(_rotm("build", str(root), "--strict", "--force", excel_on=True))
    assert _inputs_control(root)[0] == TRUE_SUM
    _ok(_rotm("check", str(root), "--strict", excel_on=True))


def test_entry_under_the_agreement_in_force_still_applies(tmp_path: Path) -> None:
    """An entry recorded under the agreement in force is used: land of the same export without --control exits 0
    with the note on the accepted totals, build writes the accepted total, check reports no D-TOTAL."""
    root = _starter(tmp_path)
    export, total = _export(root, tmp_path / "export")
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(export), "--force",
              "--control", f"lines.amount={total}"))
    assert controls.lookup(root, "lines", manifest.csv_digest(root / "raw" / "lines.csv", TYPES)[0],
                           _agreed(root)) == ([{"column": "amount", "value": total, "tolerance": 0}], None)
    out = tmp_path / "land.json"
    r = _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(export), "--json", str(out)))
    notes = "\n".join(_report(out)["notes"])
    assert "raw/lines.controls.json under the agreement in force, in place of the contract's" in notes, _out(r)
    assert EARLIER not in notes
    _ok(_rotm("build", str(root), "--strict", "--force"))
    value, source = _inputs_control(root)
    assert value == total and "raw/lines.controls.json, recorded for this snapshot" in source
    out = tmp_path / "check.json"
    _rotm("check", str(root), "--strict", "--json", str(out))
    assert not _errors(_report(out), "D-TOTAL")


def _legacy(root: Path, at: str) -> None:
    """The entry of raw/lines.controls.json as an entry written before v9: no contract_sha256, the time `at`."""
    path = controls.path(root, "lines")
    data = common.read_json(path)
    for e in data["accepted"]:
        e.pop("contract_sha256", None)
        e["at"] = at
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def test_entry_without_hash_applies_only_when_not_earlier_than_the_agreement(tmp_path: Path) -> None:
    """An entry without contract_sha256 (written before v9) applies when its time is later than the agreement's (and
    at the same second), not when it is earlier, and not when its time or the agreement's time is missing or cannot
    be read."""
    root = _starter(tmp_path)
    export, total = _export(root, tmp_path / "export")
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(export), "--force",
              "--control", f"lines.amount={total}"))
    agreed = _agreed(root)
    at = dt.datetime.fromisoformat(agreed["agreement"]["at"])
    digest = manifest.csv_digest(root / "raw" / "lines.csv", TYPES)[0]
    land_again = ("land", str(root), "--table", "lines", "--csv", str(export))
    # later: applies
    _legacy(root, (at + dt.timedelta(hours=1)).isoformat())
    assert controls.totals_for(root, "lines", digest, agreed) is not None
    _ok(_rotm(*land_again))
    # the same second: not earlier, applies
    _legacy(root, at.isoformat())
    assert controls.totals_for(root, "lines", digest, agreed) is not None
    # earlier: does not apply; land reports L-TOTAL with both totals and writes nothing
    _legacy(root, (at - dt.timedelta(days=1)).isoformat())
    assert controls.lookup(root, "lines", digest, agreed)[0] is None
    before = _three_files(root)
    r = _rotm(*land_again)
    assert r.returncode == 1 and "L-TOTAL" in r.stdout and EARLIER in r.stdout, _out(r)
    assert _three_files(root) == before
    # an entry time that cannot be read: does not apply
    _legacy(root, "not a time")
    assert controls.lookup(root, "lines", digest, agreed)[1] is not None
    assert _rotm(*land_again).returncode == 1
    # the agreement's time missing: does not apply
    _legacy(root, (at + dt.timedelta(hours=1)).isoformat())
    no_time = json.loads(json.dumps(agreed))
    no_time["agreement"].pop("at")
    assert controls.lookup(root, "lines", digest, no_time) == (None, controls.load(root, "lines")["accepted"][0])
    # with a hash the time does not count
    entry = dict(controls.load(root, "lines")["accepted"][0], contract_sha256=agreed["agreement"]["contract_sha256"])
    assert controls.applies(entry, no_time)
    assert not controls.applies(dict(entry, contract_sha256="0" * 64), agreed)


def test_show_and_agree_with_unchanged_content_keep_the_entry(tmp_path: Path) -> None:
    """contract show and contract agree with unchanged content keep the entry in force: the controls file stays
    byte for byte and land of the same export without --control still exits 0."""
    root = _starter(tmp_path)
    export, total = _export(root, tmp_path / "export")
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(export), "--force",
              "--control", f"lines.amount={total}"))
    ctl = controls.path(root, "lines").read_bytes()
    task = _task(tmp_path, "task_again", REPLY_NEW)
    _ok(_rotm("contract", "show", str(root), "--table", "lines"))
    _ok(_rotm("contract", "agree", str(root), "--table", "lines", "--reply", REPLY_NEW, "--by", "Max",
              "--channel", "task-file", "--source-file", str(task)))
    assert controls.path(root, "lines").read_bytes() == ctl
    digest = manifest.csv_digest(root / "raw" / "lines.csv", TYPES)[0]
    assert controls.totals_for(root, "lines", digest, _agreed(root))[0]["value"] == total
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(export)))
    assert controls.path(root, "lines").read_bytes() == ctl


# ============================================================================= keep and replace (part 4)

def test_the_same_values_under_the_same_agreement_keep_the_file(tmp_path: Path) -> None:
    """Recording the same values again under the same agreement keeps raw/lines.controls.json byte for byte (its
    time too)."""
    root = _starter(tmp_path)
    export, total = _export(root, tmp_path / "export")
    cmd = ("land", str(root), "--table", "lines", "--csv", str(export), "--force", "--control", f"lines.amount={total}")
    _ok(_rotm(*cmd))
    first = controls.path(root, "lines").read_bytes()
    _ok(_rotm(*cmd))
    assert controls.path(root, "lines").read_bytes() == first


def test_a_record_under_a_new_agreement_replaces_the_entry(tmp_path: Path) -> None:
    """A record for the same digest under a new agreement replaces the entry (one entry per digest, the new hash);
    build then writes the recorded total and check reports no D-TOTAL (without Excel)."""
    root = _starter(tmp_path)
    export, total = _export(root, tmp_path / "export")
    cmd = ("land", str(root), "--table", "lines", "--csv", str(export), "--force", "--control", f"lines.amount={total}")
    _ok(_rotm(*cmd))
    old = controls.load(root, "lines")["accepted"][0]
    new = _new_agreement(root, total + 1, tmp_path)
    _ok(_rotm(*cmd))
    data = controls.load(root, "lines")
    assert len(data["accepted"]) == 1
    entry = data["accepted"][0]
    assert entry["digest"] == old["digest"] and entry["contract_sha256"] == new != old["contract_sha256"]
    _ok(_rotm("build", str(root), "--strict", "--force"))
    assert _inputs_control(root)[0] == total
    out = tmp_path / "check.json"
    _rotm("check", str(root), "--strict", "--json", str(out))
    assert not _errors(_report(out), "D-TOTAL")


# ============================================================================= without Excel: the same rules

def test_build_and_check_without_excel_use_the_new_contract_total(tmp_path: Path) -> None:
    """The rule of the Excel test above without Excel: build writes the new contract's total and its source text
    names the stale total; check (saved values) reports D-TOTAL naming both totals with the land command (a static
    table), and the note."""
    root = _starter(tmp_path)
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"),
              "--control", f"lines.amount={TRUE_SUM}"))
    at = controls.load(root, "lines")["accepted"][0]["at"]
    _new_agreement(root, TRUE_SUM + 1000, tmp_path)
    _ok(_rotm("build", str(root), "--strict", "--force"))
    value, source = _inputs_control(root)
    assert value == TRUE_SUM + 1000 and f"accepted for this snapshot at {at} under an earlier agreement" in source
    out = tmp_path / "check.json"
    checked = _rotm("check", str(root), "--strict", "--json", str(out))
    assert checked.returncode == 1, _out(checked)
    report = _report(out)
    f = _errors(report, "D-TOTAL")
    assert len(f) == 1, report["findings"]
    assert f[0]["message"].endswith(f"; the total 57,514,500 accepted for these rows at {at} {EARLIER} and is not "
                                    "used"), f[0]["message"]
    assert f[0]["fix"].startswith("give the source's current total: ") and "land \"" in f[0]["fix"]
    assert any(n.startswith("raw/lines.controls.json: the total amount = 57,514,500") for n in report["notes"])


def test_acceptance_record_without_excel_is_not_used_after_a_new_agreement(tmp_path: Path, monkeypatch) -> None:
    """The record written by check --accept-refresh --control over a query table (test_check.attach_query_table
    stands in for Power Query), then the contract proposed, shown and agreed again with another total: land of the
    accepted rows without --control is L-TOTAL naming both totals; the three files stay byte for byte; check
    --accept-refresh of the same rows records the new agreement's hash when --control is given again."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    total10 = sum(100 + 7.5 * i for i in range(10))
    total12 = sum(100 + 7.5 * i for i in range(12))
    m = tc.make_model(tmp_path, 10, capacity=12, total=total10, landing="power_query", query_table=True)
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    text = f"sales.amount={total12}"
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total12}, control_texts=[text])
    assert "R-ACCEPTED" in tc.rules(rep), [f.line() for f in rep.findings]
    first = controls.load(m.root, "sales")["accepted"][0]
    agreed_first = _hash(m.root, "sales")
    contract.propose(m.root, "sales", csv=m.source, key="invoice_id,line_no", grain="one row per invoice line",
                     rows="1:100000", units=["amount=KRW", "line_no=count"], totals=[f"amount={total12 + 50}"],
                     force=True)
    contract.show(m.root, "sales")
    contract.agree(m.root, "sales", reply=tc.REPLY, by="Max", channel="task-file", source_file=m.task)
    before = _three_files(m.root, "sales")
    extract = tc.write_source_csv(tmp_path / "extract.csv", 12)
    lrep = land.land_csv(m.root, "sales", extract)
    f = [x for x in lrep.findings if x.rule == "L-TOTAL"]
    assert lrep.exit_code == 1 and len(f) == 1, [x.line() for x in lrep.findings]
    assert f[0].message.endswith(f"; the total {land._fmt(total12)} accepted for these rows at {first['at']} {EARLIER} "
                                 "and is not used"), f[0].message
    assert _three_files(m.root, "sales") == before
    assert first["contract_sha256"] == agreed_first
    # check without --control: D-TOTAL against the new contract's total names the stale one and the query way on
    rep = check.check_model(m.root)
    d = [x for x in rep.findings if x.rule == "D-TOTAL"]
    assert d and EARLIER in d[0].message and "--accept-refresh --control sales.amount=<source total>" in d[0].fix
    assert any(EARLIER in n for n in rep.notes)


def test_lift_uses_the_contract_total_and_notes_a_stale_entry(tmp_path: Path) -> None:
    """lift after the new agreement: the script's control total is the contract's and lift's report holds the
    note."""
    root = _starter(tmp_path)
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"),
              "--control", f"lines.amount={TRUE_SUM}"))
    _new_agreement(root, TRUE_SUM + 1000, tmp_path)
    _ok(_rotm("build", str(root), "--strict", "--force"))
    report = lift.lift_model(root)
    assert any(n.startswith("raw/lines.controls.json: the total amount = 57,514,500") and EARLIER in n
               for n in report.notes), report.notes
    import importlib.util
    path = Path(report.data["script"])
    spec = importlib.util.spec_from_file_location("lifted_v9", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.INFO["tables"]["lines"]["expect"]["totals"][0]["value"] == TRUE_SUM + 1000


def test_schema_accepts_the_hash_and_entries_without_it() -> None:
    good = {"rotm_controls": 1, "table": "lines", "accepted": [
        {"digest": "sha256:" + "1" * 64, "rows": 3, "totals": [{"column": "amount", "value": 1.5, "tolerance": 0}],
         "given": ["lines.amount=1.5"], "at": "2026-11-25T08:00:00+09:00"}]}
    from lib import schemas
    assert schemas.validate(good, "controls") == []
    good["accepted"][0]["contract_sha256"] = "a" * 64
    assert schemas.validate(good, "controls") == []
    good["accepted"][0]["contract_sha256"] = "sha256:" + "a" * 64
    assert [e.path for e in schemas.validate(good, "controls")] == ["accepted[0].contract_sha256"]

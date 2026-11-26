"""Tests of the v9 entry [A-aera-4, A-aera-7] (package P2 of the v9 plan). Owner: P2.

- [A-aera-4] (1): --control for a column for which the agreed contract names no control total is refused by land
  and by check --accept-refresh (exit 2; nothing written or recorded). With the total in the agreed contract the same
  land command lands, and build writes the check <t>_total_<column>. check --control without --accept-refresh stays
  as in v8.
- [A-aera-7] (2): land decides key uniqueness and the count of agreed key repeats on the key values as raw/<t>.csv
  stores them; two source values stored as one value are L-KEY naming both lines and both values; nothing is written.
  A snapshot that land wrote lands again with the same result, and the six shipped folders land with unchanged
  digests.

The names of the first three tests are fixed by the v9 plan (section 5.3) and quoted by the documents.
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

from lib import common, contract, controls, land, manifest, modelfolder
import test_check as tc

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
PY = [sys.executable, "-P", "-X", "utf8", "-B"]
ROTM_CMD = PY + [str(SCRIPTS / "rotm.py")]
REPLY = "agreed, land it as proposed."
REFUSED = ("--control {item}: the agreed contract of '{t}' names no control total for column '{c}'; a control total "
           "is part of the agreement; nothing was {done}")


def _rotm(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ, ROTM_NO_EXCEL="1", **(env or {}))
    return subprocess.run(ROTM_CMD + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=e, timeout=900)


def _out(r: subprocess.CompletedProcess[str]) -> str:
    return ((r.stdout or "") + (r.stderr or ""))[-6000:]


def _ok(r: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
    assert r.returncode == 0, _out(r)
    return r


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return path


def _tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def _starter(tmp_path: Path, *, total: bool) -> tuple[Path, Path, int]:
    """A copy of the starter (init --template starter) whose contract is proposed again from data/lines.csv with a
    row count and, when `total`, the control total of amount; shown and agreed through the task-file channel; then
    data/lines.csv landed. Returns (model folder, an export with 3 rows more, the export's sum of amount)."""
    root = tmp_path / "model"
    _ok(_rotm("init", str(root), "--template", "starter"))
    rows = list(csv.reader(open(root / "data" / "lines.csv", encoding="utf-8-sig", newline="")))
    true_sum = sum(int(float(r[3])) for r in rows[1:])
    extra = ["--total", f"amount={true_sum}"] if total else []
    _ok(_rotm("contract", "propose", str(root), "--table", "lines", "--force", "--csv", str(root / "data" / "lines.csv"),
              "--key", "line_id", "--type", "amount=number", "--unit", "amount=KRW", "--rows", "1:10000", *extra))
    _ok(_rotm("contract", "show", str(root), "--table", "lines"))
    task = tmp_path / "task.md"
    task.write_text(f"# Task\n\nContract reply from the user: {REPLY}\n", encoding="utf-8")
    _ok(_rotm("contract", "agree", str(root), "--table", "lines", "--reply", REPLY, "--by", "Max",
              "--channel", "task-file", "--source-file", str(task)))
    c = common.read_json(root / "contracts" / "lines.contract.json")
    assert c["expect"]["rows"] == {"min": 1, "max": 10000}
    assert bool(c["expect"].get("totals")) is total
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"), "--force"))
    more = rows + [[f"L9{i:03d}", "2026-03-15", "Rent", str(100000 * (i + 1))] for i in range(3)]
    export = _csv(tmp_path / "export" / "lines.csv", more[0], more[1:])
    return root, export, sum(int(float(r[3])) for r in more[1:])


# ============================================================================= [A-aera-4] fixed names (plan 5.3)

def test_control_without_a_contract_total_is_refused_by_land(tmp_path: Path) -> None:
    """A contract with a row count and no total: land --csv --force --control exits 2, the refusal says that a
    control total is part of the agreement and gives the way, and raw/ stays byte for byte (no controls file). v8
    landed the export and recorded the total."""
    root, export, total = _starter(tmp_path, total=False)
    before = _tree(root / "raw")
    item = f"lines.amount={total}"
    r = _rotm("land", str(root), "--table", "lines", "--csv", str(export), "--force", "--control", item)
    assert r.returncode == 2, _out(r)
    assert REFUSED.format(item=item, t="lines", c="amount", done="written") in r.stderr, r.stderr
    assert (f"add the total to the contract (rotm.py contract propose \"{root.as_posix()}\" --table lines --force "
            "... --total amount=<source total>), show it to the user and get it agreed; build then writes its check "
            "lines_total_amount") in r.stderr, r.stderr
    assert "Traceback" not in r.stderr
    assert _tree(root / "raw") == before and not controls.path(root, "lines").exists()


def test_control_without_a_contract_total_is_refused_by_accept_refresh(tmp_path: Path) -> None:
    """check --accept-refresh --control for a column without a contract total exits 2 before Excel starts and
    records nothing: raw/, the workbook, the manifest and reports/status.json stay byte for byte. check --control
    without --accept-refresh stays as in v8 (it runs and compares the sum with the total given)."""
    m = tc.make_model(tmp_path, 10, capacity=12, landing="power_query", query_table=True)      # no contract total
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))                      # stands in for a refresh with 2 more rows
    total = sum(100 + 7.5 * i for i in range(12))
    status = m.root / "reports" / "status.json"
    watched = [m.wb, manifest.manifest_path(m.wb), status]
    before = (_tree(m.root / "raw"), {p.name: (p.read_bytes() if p.is_file() else None) for p in watched})
    item = f"sales.amount={total}"
    r = tc.run_cli("check", str(m.root), "--accept-refresh", "--control", item, env_extra={"ROTM_NO_EXCEL": "1"})
    assert r.returncode == 2, _out(r)
    assert REFUSED.format(item=item, t="sales", c="amount", done="recorded") in r.stderr, r.stderr
    assert "build then writes its check sales_total_amount" in r.stderr
    after = (_tree(m.root / "raw"), {p.name: (p.read_bytes() if p.is_file() else None) for p in watched})
    assert after == before and not controls.path(m.root, "sales").exists()
    # without --accept-refresh: as in v8 (no refusal; the given total is compared)
    r = tc.run_cli("check", str(m.root), "--control", item, env_extra={"ROTM_NO_EXCEL": "1"})
    assert r.returncode != 2 and "names no control total" not in _out(r), _out(r)
    assert not controls.path(m.root, "sales").exists()


def test_contracted_total_lands_and_build_writes_its_check(tmp_path: Path) -> None:
    """With the total in the agreed contract the same land command lands (exit 0, the total recorded), and build
    writes the check lines_total_amount (the manifest's checks and the checks sheet)."""
    import openpyxl
    root, export, total = _starter(tmp_path, total=True)
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(export), "--force", "--control",
              f"lines.amount={total}"))
    assert controls.load(root, "lines")["accepted"][0]["totals"][0]["value"] == total
    _ok(_rotm("build", str(root), "--strict", "--force"))
    man = manifest.load_manifest(root / "model.xlsx")
    ids = [c["id"] for c in man["checks"]]
    assert "lines_total_amount" in ids, ids
    ws = openpyxl.load_workbook(root / "model.xlsx")["checks"]
    assert "lines_total_amount" in [ws.cell(r, 2).value for r in range(1, ws.max_row + 1)]
    # and without the total in the contract, build writes no such check
    root2, _export2, _t = _starter(tmp_path / "no_total", total=False)
    _ok(_rotm("build", str(root2), "--strict", "--force"))
    assert "lines_total_amount" not in [c["id"] for c in manifest.load_manifest(root2 / "model.xlsx")["checks"]]


def test_accept_refresh_records_only_contract_total_columns(tmp_path: Path, monkeypatch) -> None:
    """With a contract total for amount, check --accept-refresh --control for amount records amount only (the v8
    loop over other --control columns is gone; such a column is refused before)."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    from lib import check
    m = tc.make_model(tmp_path, 10, capacity=12, total=sum(100 + 7.5 * i for i in range(10)), landing="power_query",
                      query_table=True)
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    total = sum(100 + 7.5 * i for i in range(12))
    with pytest.raises(common.Refusal, match="names no control total for column 'line_no'"):
        check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total, "sales.line_no": 18},
                          control_texts=[f"sales.amount={total}", "sales.line_no=18"])
    assert not controls.path(m.root, "sales").exists()
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total},
                            control_texts=[f"sales.amount={total}"])
    assert "R-ACCEPTED" in tc.rules(rep), [f.line() for f in rep.findings]
    assert [t["column"] for t in controls.load(m.root, "sales")["accepted"][0]["totals"]] == ["amount"]


# ============================================================================= [A-aera-7] keys as the snapshot stores

def _number_key_model(tmp_path: Path) -> Path:
    root = tmp_path / "keys"
    modelfolder.init_model(root, name="v9_keys")
    sample = _csv(tmp_path / "sample" / "k.csv", ["id", "amount"], [["1.5", "10"], ["2.5", "20"], ["3.5", "30"]])
    contract.propose(root, "k", csv=sample, key="id", grain="one row per id", rows="1:100",
                     types=["id=number", "amount=number"])
    task = tmp_path / "task.md"
    task.write_text(f"# Task\n\nContract reply from the user: {REPLY}\n", encoding="utf-8")
    contract.agree(root, "k", reply=REPLY, by="Max", channel="task-file", source_file=task)
    return root


def test_number_keys_that_differ_in_the_16th_digit_are_refused_with_both_lines(tmp_path: Path) -> None:
    """1.123456789012341 and 1.123456789012342 are one value in raw/k.csv (15 significant digits): L-KEY names lines
    2 and 3, both original values and the stored value; exit 1, nothing written. v8 landed both rows, and the
    snapshot then held a repeated key that check reported as D-KEY."""
    root = _number_key_model(tmp_path)
    src = _csv(tmp_path / "src" / "k.csv", ["id", "amount"],
               [["1.123456789012341", "10"], ["1.123456789012342", "20"], ["7", "30"]])
    out = tmp_path / "land.json"
    r = _rotm("land", str(root), "--table", "k", "--csv", str(src), "--json", str(out))
    assert r.returncode == 1, _out(r)
    report = json.loads(out.read_text(encoding="utf-8"))
    keys = [f for f in report["findings"] if f["rule"] == "L-KEY"]
    assert len(keys) == 1, report["findings"]
    stored = land.snapshot_text(1.123456789012341, "number")
    assert stored == land.snapshot_text(1.123456789012342, "number") == "1.12345678901234"
    assert keys[0]["message"] == (
        "k.csv: lines 2 and 3: the key values '1.123456789012341' and '1.123456789012342' are stored as "
        "'1.12345678901234' in raw/k.csv (the snapshot keeps 15 significant digits), so the rows repeat a key (id)")
    assert keys[0]["fix"] == land.MERGED_KEY_FIX
    assert not (root / "raw" / "k.csv").exists()
    # the same rows with the key as text land (the fix of the finding)
    root2 = tmp_path / "text_key"
    modelfolder.init_model(root2, name="v9_text_keys")
    contract.propose(root2, "k", csv=src, key="id", grain="one row per id", rows="1:100", types=["id=text"])
    contract.agree(root2, "k", reply=REPLY, by="Max", channel="task-file", source_file=tmp_path / "task.md")
    assert land.land_csv(root2, "k", src).exit_code == 0


def test_a_repeated_key_and_an_agreed_repeat_are_counted_on_the_stored_values(tmp_path: Path) -> None:
    """A real repeat (the same text twice) is the v8 L-KEY and counts toward expect.key_repeats; a repeat that only
    the cut makes is L-KEY of the stored values even when the contract agrees one repeat as known."""
    root = _number_key_model(tmp_path)
    c = common.read_json(root / "contracts" / "k.contract.json")
    rep = common.Report("land", "probe")
    body = [["4.5", "1"], ["4.5", "2"], ["8", "3"]]
    land.validate_rows({**c, "expect": {"key_repeats": {"max": 1}}}, ["id", "amount"], body, rep, source="k.csv")
    assert [f.rule for f in rep.findings] == ["L-KNOWN"], [f.line() for f in rep.findings]
    rep = common.Report("land", "probe")
    body = [["1.123456789012341", "1"], ["1.123456789012342", "2"], ["8", "3"]]
    land.validate_rows({**c, "expect": {"key_repeats": {"max": 1}}}, ["id", "amount"], body, rep, source="k.csv")
    errs = [f for f in rep.findings if f.severity == "error"]
    assert [f.rule for f in errs] == ["L-KEY"] and "are stored as '1.12345678901234'" in errs[0].message


def test_datetime_keys_cut_to_the_second_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "dt"
    modelfolder.init_model(root, name="v9_dt_keys")
    sample = _csv(tmp_path / "sample" / "e.csv", ["at", "n"], [["2026-01-01 10:00:00", "1"],
                                                               ["2026-01-01 11:00:00", "2"]])
    contract.propose(root, "e", csv=sample, key="at", grain="one row per event", rows="1:100",
                     types=["at=datetime", "n=integer"])
    task = tmp_path / "task.md"
    task.write_text(f"# Task\n\nContract reply from the user: {REPLY}\n", encoding="utf-8")
    contract.agree(root, "e", reply=REPLY, by="Max", channel="task-file", source_file=task)
    src = _csv(tmp_path / "src" / "e.csv", ["at", "n"], [["2026-01-01 10:00:00.200", "1"],
                                                         ["2026-01-01 10:00:00.700", "2"]])
    rep = land.land_csv(root, "e", src)
    errs = [f for f in rep.findings if f.rule == "L-KEY"]
    assert rep.exit_code == 1 and len(errs) == 1, [f.line() for f in rep.findings]
    assert "lines 2 and 3" in errs[0].message and "'2026-01-01 10:00:00.200'" in errs[0].message
    assert "'2026-01-01 10:00:00.700'" in errs[0].message and "a fraction of a second was cut" in errs[0].message
    assert not (root / "raw" / "e.csv").exists()


def _shipped() -> list[Path]:
    roots = [SKILL / "examples", SKILL / "templates"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        roots.append(PLUGIN / "templates")
    return [p for r in roots if r.is_dir() for p in sorted(r.iterdir()) if (p / "model.spec.json").is_file()]


SHIPPED = _shipped()


@pytest.mark.parametrize("folder", SHIPPED, ids=[f"{p.parent.name}/{p.name}" for p in SHIPPED])
def test_a_snapshot_land_wrote_lands_again_with_the_same_result(folder: Path, outside_tmp: Path) -> None:
    """Each shipped folder (copied): raw/<t>.csv, a snapshot land wrote, landed again from a copy of itself exits 0
    with 'nothing changed' and the same digest, and from data/<t>.csv after the snapshot was removed gives the same
    bytes (the rule of test_v8_land.py::test_shipped_folders_land_with_the_same_digests)."""
    dest = outside_tmp / folder.name
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "reports"))
    env = {"ROTM_EXAMPLE_ROOTS": str(outside_tmp), "PYTHONUTF8": "1"}
    for t in modelfolder.tables(folder):
        snap = dest / "raw" / f"{t}.csv"
        types = [c["type"] for c in common.read_json(dest / "contracts" / f"{t}.contract.json")["columns"]]
        digest = manifest.csv_digest(snap, types)[0]
        again = shutil.copy2(snap, outside_tmp / f"{folder.name}_{t}_again.csv")
        r = _rotm("land", str(dest), "--table", t, "--csv", str(again), env=env)
        assert r.returncode == 0 and "nothing changed" in r.stdout, (t, _out(r))
        assert manifest.csv_digest(snap, types)[0] == digest
        snap.unlink()
        r = _rotm("land", str(dest), "--table", t, "--csv", str(dest / "data" / f"{t}.csv"), env=env)
        assert r.returncode == 0, (t, _out(r))
        assert snap.read_bytes() == (folder / "raw" / f"{t}.csv").read_bytes(), t

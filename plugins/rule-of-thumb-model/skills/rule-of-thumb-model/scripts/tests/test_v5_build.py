"""Tests of the v5 build changes (package P2 of the v5 plan). Owner: P2.

Entries: [J-1] (an id used by two kinds is refused at load with both JSON paths, or builds; never exit 4),
[A-aera-9, C-sol-14, J-2] (the worked example's percentage checks in the product form, the placeholder
{blk:<block>.<item>}, the Difference cell of a date check), [A-aera-15] (I-SPARSE-SHEET), and the supporting
changes: the valid-range refusal [A-aera-1], the tolerance refusals and the check formulas in the manifest
[A-aera-3, C-sol-5], declared text inputs written as text [A-aera-16, A-aera-17], and the build preflight of a
generated query [A-aera-2, C-sol-1, C-sol-4]. Tests marked excel drive Excel through lib.excel. Tests that read the
plugin's templates skip when the skill folder is copied alone (plan section 7, I-8).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from lib import build, common, manifest, modelfolder

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
EXAMPLE = SKILL / "examples" / "sales_vs_target"
STARTER = SKILL / "templates" / "starter"
PLUGIN_TEMPLATES = PLUGIN / "templates"
SHIPPED_PLUGIN = ("ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast")


def rotm(*args: object, timeout: float = 900, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = dict(os.environ)
    full.update(env or {})
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=full)


def out(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def copy_model(src: Path, dest: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of a shipped model folder without its built files; its 'example' agreements count under the copy's
    parent folder (ROTM_EXAMPLE_ROOTS)."""
    from lib import spec as specmod
    monkeypatch.setenv(specmod.EXAMPLE_ROOTS_ENV, str(dest.parent))
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.xlsx", "*.manifest.json", "reports", "lift",
                                                             "__pycache__", "*.pyc"))
    return dest


def agree_again(root: Path, table: str, tmp_path: Path) -> None:
    """Show a changed contract and record the agreement through the task-file channel (a headless test)."""
    shown = rotm("contract", "show", root, "--table", table)
    assert shown.returncode == 0, out(shown)
    task = tmp_path / f"task_{table}.md"
    task.write_text("# Task\n\nContract agreement: pre-agreed for this test\n", encoding="utf-8")
    agree = rotm("contract", "agree", root, "--table", table, "--reply", "pre-agreed for this test", "--by",
                 "tester", "--channel", "task-file", "--source-file", task)
    assert agree.returncode == 0, out(agree)
    assert modelfolder.contract_state(root, table) == "agreed"


def example_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "sales_vs_target") -> Path:
    return copy_model(EXAMPLE, tmp_path / name, monkeypatch)


def read_spec(root: Path) -> dict[str, Any]:
    return common.read_json(root / "model.spec.json")


def write_spec(root: Path, data: dict[str, Any]) -> None:
    common.write_json(root / "model.spec.json", data)


def sheet_of(data: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in data["sheets"] if s["name"] == name)


def check_of(data: dict[str, Any], cid: str) -> dict[str, Any]:
    return next(c for s in data["sheets"] if s["type"] == "checks" for c in s["checks"] if c["id"] == cid)


def block_of(data: dict[str, Any], bid: str) -> dict[str, Any]:
    return next(b for s in data["sheets"] for b in s.get("blocks", []) if b["id"] == bid)


# ============================================================================= [J-1]
#
# The worked example's sheets: [0] inputs, [1] raw_sales, [2] raw_target, [3] h_sales, [4] h_target, [5] calc,
# [6] summary, [7] checks, [8] notes. Each change below gives one id to two kinds.

def _add_input(iid: str) -> Callable[[dict], None]:
    def change(d: dict) -> None:
        sheet_of(d, "inputs")["items"].append({"id": iid, "label": f"Input {iid}", "value": 1, "type": "number",
                                               "source": "test"})
    return change


def _add_helper(cid: str) -> Callable[[dict], None]:
    def change(d: dict) -> None:
        sheet_of(d, "h_sales")["columns"].append({"id": cid, "label": f"Column {cid}", "expr": "{this:amount}",
                                                  "format": "amount"})
    return change


def _rename_check(old: str, new: str) -> Callable[[dict], None]:
    def change(d: dict) -> None:
        check_of(d, old)["id"] = new
    return change


def _rename_sheet(old: str, new: str) -> Callable[[dict], None]:
    def change(d: dict) -> None:
        sheet_of(d, old)["name"] = new
    return change


def _key_item_named_like_a_block(d: dict) -> None:
    item = next(i for i in block_of(d, "bridge")["items"] if i["id"] == "cum")
    item["id"] = "variance"                                   # a key output with the id of the block 'variance'
    check_of(d, "cum_ties")["actual"] = "{cell:variance}"


# (kind A, kind B) -> (change, refused?, the two JSON paths of a refused pair)
ID_PAIRS: dict[tuple[str, str], tuple[Callable[[dict], None], bool, tuple[str, str] | None]] = {
    ("input", "block"): (_add_input("bridge"), True, ("sheets[0].items[4].id", "sheets[5].blocks[4].id")),
    ("input", "cell"): (_add_input("cum"), False, None),
    ("input", "check"): (_add_input("cum_ties"), True, ("sheets[0].items[4].id", "sheets[7].checks[3].id")),
    ("input", "helper column"): (_add_input("amount_k"), False, None),
    ("input", "sheet"): (_add_input("summary"), False, None),
    ("block", "cell"): (_key_item_named_like_a_block, False, None),
    ("block", "check"): (_rename_check("cum_ties", "bridge"), True, ("sheets[5].blocks[4].id",
                                                                     "sheets[7].checks[3].id")),
    ("block", "helper column"): (_add_helper("bridge"), False, None),
    ("block", "sheet"): (_rename_sheet("summary", "ytd"), False, None),
    ("cell", "check"): (_rename_check("pct_cells", "pct"), False, None),
    ("cell", "helper column"): (_add_helper("cum"), False, None),
    ("cell", "sheet"): (_rename_sheet("summary", "pct"), False, None),
    ("check", "helper column"): (_rename_check("pct_cells", "amount_k"), False, None),
    ("check", "sheet"): (_rename_check("pct_cells", "summary"), False, None),
    ("helper column", "sheet"): (_add_helper("calc"), False, None),
}


@pytest.mark.parametrize("pair", list(ID_PAIRS), ids=[f"{a}-{b}" for a, b in ID_PAIRS])
def test_id_pairs_build_or_are_refused_with_both_paths(pair: tuple[str, str], tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    change, refused, paths = ID_PAIRS[pair]
    root = example_copy(tmp_path, monkeypatch)
    data = read_spec(root)
    change(data)
    write_spec(root, data)
    proc = rotm("build", root, "--force")
    text = out(proc)
    assert proc.returncode != 4 and "internal error" not in text, text
    if refused:
        assert proc.returncode == 2, text
        assert paths is not None and paths[0] in text and paths[1] in text, text
        assert "rename one of them: inputs, blocks and checks share one set of ids" in text, text
        assert not (root / "model.xlsx").exists()
    else:
        assert proc.returncode == 0, text
        assert (root / "model.xlsx").is_file()


@pytest.mark.parametrize("block_id,block_path", [("bridge", "sheets[5].blocks[4].id"), ("ytd", "sheets[6].blocks[0].id"),
                                                 ("actual", "sheets[5].blocks[0].id")])
def test_input_with_the_id_of_an_items_or_measures_block_is_refused(block_id: str, block_path: str, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The user test of round 4: an input with the id of a block ended with KeyError, exit 4 (items and measures
    blocks), or with follow-on lines 'unknown block' (grid blocks)."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = example_copy(tmp_path, monkeypatch)
    data = read_spec(root)
    _add_input(block_id)(data)
    write_spec(root, data)
    proc = rotm("build", root, "--force")
    text = out(proc)
    assert proc.returncode == 2, text
    assert (f"{block_path}: id '{block_id}' is used by an input at sheets[0].items[4].id and by a block at "
            f"{block_path}") in text, text
    assert ("rename one of them: inputs, blocks and checks share one set of ids (references/model-spec-format.md, "
            "\"Ids\")") in text, text
    assert "model.spec.json has 1 problem;" in text, text
    assert "unknown block" not in text and "unknown input" not in text and "KeyError" not in text, text


def test_input_with_the_id_of_a_generated_check_names_both_paths(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = example_copy(tmp_path, monkeypatch)
    data = read_spec(root)
    _add_input("sales_rows")(data)
    write_spec(root, data)
    proc = rotm("build", root, "--force")
    text = out(proc)
    assert proc.returncode == 2, text
    assert ("id 'sales_rows' is used by the check build adds for raw table 'sales' at sheets[1].table and by an input "
            "at sheets[0].items[4].id") in text, text


def test_two_blocks_with_one_id_and_a_measure_placeholder_are_refused_without_an_internal_error(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second block named like an items block reads its own measures ({m:}), so no KeyError is raised."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = example_copy(tmp_path, monkeypatch)
    data = read_spec(root)
    block_of(data, "ytd")["id"] = "bridge"
    write_spec(root, data)
    proc = rotm("build", root, "--force")
    text = out(proc)
    assert proc.returncode == 2, text
    assert "id 'bridge' is used by a block at sheets[5].blocks[4].id and by a block at sheets[6].blocks[0].id" in text
    assert "KeyError" not in text and "internal error" not in text, text


def test_input_with_the_id_of_a_contract_control_names_both_places(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract controls build adds on the inputs sheet are inputs: an input with such an id is refused with
    the input's path and the contract's; a block or a check with such an id builds (their own tables)."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = copy_model(STARTER, tmp_path / "starter", monkeypatch)
    data = read_spec(root)
    _add_input("lines_rows_min")(data)
    write_spec(root, data)
    proc = rotm("build", root, "--force")
    text = out(proc)
    assert proc.returncode == 2, text
    assert ("sheets[0].items[2].id: input id 'lines_rows_min' is the id build gives a contract control of table "
            "'lines' (from contracts/lines.contract.json expect.rows.min)") in text, text
    assert "rename the input" in text, text
    other = copy_model(STARTER, tmp_path / "s2" / "starter", monkeypatch)
    data = read_spec(other)
    check_of(data, "spend_ties_raw")["id"] = "lines_rows_min"
    write_spec(other, data)
    proc = rotm("build", other, "--force")
    assert proc.returncode == 0, out(proc)


# ============================================================================= [A-aera-9, C-sol-14, J-2]

def _built(root: Path, *, strict: bool = True) -> Any:
    rep = build.build(root / "model.spec.json", strict=strict, force=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    return rep


def _check_cells(root: Path, cid: str) -> dict[str, Any]:
    """The cells of one check row of the built workbook: column letter -> openpyxl cell."""
    import openpyxl
    data = manifest.load_manifest(root / "model.xlsx")
    entry = next(c for c in data["checks"] if c["id"] == cid)
    sheet, cell = entry["result"].replace("$", "").split("!")
    row = int("".join(ch for ch in cell if ch.isdigit()))
    ws = openpyxl.load_workbook(root / "model.xlsx")[sheet.strip("'")]
    return {col: ws[f"{col}{row}"] for col in "BCDEFGHI"}


def _bridge_rows(root: Path) -> tuple[dict[str, int], str, str]:
    data = manifest.load_manifest(root / "model.xlsx")
    bridge = next(b for s in data["sheets"] for b in s.get("blocks") or [] if b["id"] == "bridge")
    first, last = bridge["body"].split(":")
    c1 = "".join(ch for ch in first if ch.isalpha())
    c2 = "".join(ch for ch in last if ch.isalpha())
    return {it["id"]: it["row"] for it in bridge["items"]}, c1, c2


def _item_range(rows: dict[str, int], c1: str, c2: str, item: str) -> str:
    return f"calc!${c1}${rows[item]}:${c2}${rows[item]}"


def test_example_checks_divide_by_no_denominator_and_name_no_row_number() -> None:
    data = read_spec(EXAMPLE)
    ytd = check_of(data, "ytd_pct_ties")
    assert ytd["actual"] == "{cell:pct}*{blk:target.total}"
    assert ytd["expected"] == "--({blk:target.total}<>0)*{blk:variance.total}"
    assert "/" not in ytd["actual"] + ytd["expected"]
    assert "target not 0" in ytd["label"] and len(ytd["label"]) <= 80
    assert "A target total of 0 gives 0 %" in ytd["note"]
    var = check_of(data, "var_pct_all")
    assert var["actual"] == "SUMPRODUCT({blk:bridge.var_pct},{blk:bridge.tgt})"
    assert var["expected"] == "SUMPRODUCT(--({blk:bridge.tgt}<>0),{blk:bridge.var})"
    assert "INDEX" not in var["actual"] + var["expected"] and "row 1" not in var["note"]
    assert all(len(c["label"]) <= 80 for s in data["sheets"] if s["type"] == "checks" for c in s["checks"])
    task = (EXAMPLE / "task.md").read_text(encoding="utf-8")
    assert "change the check with it" not in task and "1+1" not in task
    assert "{blk:bridge.var_pct}" in task


def test_blk_item_placeholder_reads_the_item_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = example_copy(tmp_path, monkeypatch)
    _built(root)
    rows, c1, c2 = _bridge_rows(root)
    cells = _check_cells(root, "var_pct_all")
    vp, tg, va = (_item_range(rows, c1, c2, i) for i in ("var_pct", "tgt", "var"))
    assert cells["D"].value == f"=SUMPRODUCT({vp},{tg})", cells["D"].value
    assert cells["E"].value == f"=SUMPRODUCT(--({tg}<>0),{va})", cells["E"].value
    ytd = _check_cells(root, "ytd_pct_ties")
    assert "/" not in str(ytd["D"].value) + str(ytd["E"].value)


def test_blk_item_placeholder_is_refused_for_an_unknown_item_or_a_grid_block(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import spec as specmod
    root = example_copy(tmp_path, monkeypatch)
    data = read_spec(root)
    check_of(data, "var_pct_all")["actual"] = "SUMPRODUCT({blk:bridge.var_pc},{blk:bridge.tgt})"
    check_of(data, "var_pct_all")["expected"] = "SUMPRODUCT(--({blk:actual.East}<>0),{blk:bridge.var})"
    check_of(data, "pct_cells")["actual"] = "COUNT({blk:ytd.act})"
    write_spec(root, data)
    with pytest.raises(common.Refusal) as exc:
        specmod.load_spec(root)
    assert exc.value.exit_code == 2
    text = "\n".join(exc.value.details)
    assert "unknown item 'var_pc' of block 'bridge' in {blk:bridge.var_pc}; did you mean 'var_pct'?" in text, text
    assert "{blk:actual.East}: block 'actual' is a grid block; {blk:<block>.<item>} names a line item of an items " \
           "block" in text, text
    assert "{blk:ytd.act}: block 'ytd' is a measures block" in text, text
    assert len(exc.value.details) == 3, text


def _reordered_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[str]]:
    root = example_copy(tmp_path, monkeypatch)
    data = read_spec(root)
    bridge = block_of(data, "bridge")
    bridge["items"] = list(reversed(bridge["items"]))       # cum, var_pct, var, tgt, act
    write_spec(root, data)
    return root, [i["id"] for i in bridge["items"]]


def test_reordered_bridge_items_move_the_check_without_excel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root, order = _reordered_example(tmp_path, monkeypatch)
    _built(root)
    rows, c1, c2 = _bridge_rows(root)
    assert sorted(rows, key=rows.get) == order
    cells = _check_cells(root, "var_pct_all")
    vp, tg, va = (_item_range(rows, c1, c2, i) for i in ("var_pct", "tgt", "var"))
    assert cells["D"].value == f"=SUMPRODUCT({vp},{tg})" and cells["E"].value == f"=SUMPRODUCT(--({tg}<>0),{va})"


@pytest.mark.excel
def test_reordered_bridge_items_keep_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import openpyxl
    root, order = _reordered_example(tmp_path, monkeypatch)
    proc = rotm("build", root, "--strict", "--force")
    assert proc.returncode == 0, out(proc)
    rows, c1, c2 = _bridge_rows(root)
    assert sorted(rows, key=rows.get) == order
    cells = _check_cells(root, "var_pct_all")
    vp, tg, va = (_item_range(rows, c1, c2, i) for i in ("var_pct", "tgt", "var"))
    assert cells["D"].value == f"=SUMPRODUCT({vp},{tg})" and cells["E"].value == f"=SUMPRODUCT(--({tg}<>0),{va})"
    chk = rotm("check", root, "--strict")
    assert chk.returncode == 0, out(chk)
    assert "checks: 24 of 24 hold" in out(chk), out(chk)
    values = openpyxl.load_workbook(root / "model.xlsx", data_only=True)
    entry = next(c for c in manifest.load_manifest(root / "model.xlsx")["checks"] if c["id"] == "var_pct_all")
    sheet, cell = entry["result"].replace("$", "").split("!")
    assert values[sheet][cell].value == 0
    row = int("".join(ch for ch in cell if ch.isdigit()))
    actual, expected = values[sheet][f"D{row}"].value, values[sheet][f"E{row}"].value
    assert isinstance(actual, float) and abs(actual - expected) < 0.001 and abs(expected) > 1, (actual, expected)


def _shipped_specs() -> list[Path]:
    found = sorted(SKILL.glob("examples/*/model.spec.json")) + sorted(SKILL.glob("templates/*/model.spec.json"))
    if PLUGIN_TEMPLATES.is_dir():
        found += sorted(PLUGIN_TEMPLATES.glob("*/model.spec.json"))
    return found


def test_no_shipped_spec_builds_a_row_number_from_added_ones() -> None:
    import re
    specs = _shipped_specs()
    assert len(specs) >= 2
    added = re.compile(r"(?<![\w.])1\+1(?![\w.])")
    held = [p.as_posix().split("/")[-2] for p in specs if added.search(p.read_text(encoding="utf-8"))]
    assert held == [], held


@pytest.mark.excel
def test_example_with_every_target_zero_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import csv
    root = example_copy(tmp_path, monkeypatch)
    with open(root / "data" / "target.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows and "target_amount" in rows[0]
    for r in rows:
        r["target_amount"] = "0"
    with open(root / "data" / "target.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    cpath = root / "contracts" / "target.contract.json"
    contract = common.read_json(cpath)
    totals = contract["expect"]["totals"]
    assert [t["column"] for t in totals] == ["target_amount"]
    totals[0]["value"] = 0
    common.write_json(cpath, contract)
    agree_again(root, "target", tmp_path)
    landed = rotm("land", root, "--table", "target", "--csv", root / "data" / "target.csv", "--force")
    assert landed.returncode == 0, out(landed)
    proc = rotm("build", root, "--strict", "--force")
    assert proc.returncode == 0, out(proc)
    chk = rotm("check", root, "--strict")
    assert chk.returncode == 0, out(chk)
    assert "checks: 24 of 24 hold" in out(chk), out(chk)
    aud = rotm("audit", root, "--strict")
    assert aud.returncode == 0, out(aud)


def _date_check_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[dict] | None = None) -> Path:
    root = copy_model(STARTER, tmp_path / "starter", monkeypatch)
    data = read_spec(root)
    base = {"id": "start_is_month_end", "kind": "equal", "format": "date", "label": "The first month is a month end",
            "actual": "{in:start_month}", "expected": "EOMONTH({in:start_month},0)"}
    sheet_of(data, "checks")["checks"].append(base)
    for e in extra or []:
        sheet_of(data, "checks")["checks"].append(dict(base, **e))
    write_spec(root, data)
    return root


def test_date_check_difference_is_a_number_of_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import dictionary
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _date_check_model(tmp_path, monkeypatch)
    _built(root)
    d = dictionary.load_for_model(root)
    cells = _check_cells(root, "start_is_month_end")
    assert cells["D"].number_format == d.number_format("date")
    assert cells["E"].number_format == d.number_format("date")
    assert cells["G"].number_format == d.number_format("count")
    assert cells["H"].number_format == "General"
    assert str(cells["G"].value).startswith("=")
    aud = rotm("audit", root, "--strict")
    assert aud.returncode == 0, out(aud)


def test_datetime_and_month_checks_get_a_number_difference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import dictionary
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _date_check_model(tmp_path, monkeypatch, [
        {"id": "start_month_again", "format": "month", "label": "The first month again"},
        {"id": "start_time", "format": "datetime", "label": "The first month as a date and time"}])
    _built(root)
    d = dictionary.load_for_model(root)
    month = _check_cells(root, "start_month_again")
    assert (month["D"].number_format, month["G"].number_format) == (d.number_format("month"), d.number_format("count"))
    stamp = _check_cells(root, "start_time")
    assert stamp["E"].number_format == d.number_format("datetime")
    assert stamp["G"].number_format == d.number_format("amount2")


@pytest.mark.excel
def test_date_check_shows_dates_and_zero_days_after_excel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt
    import openpyxl
    root = _date_check_model(tmp_path, monkeypatch)
    proc = rotm("build", root, "--strict", "--force")
    assert proc.returncode == 0, out(proc)
    chk = rotm("check", root, "--strict")
    assert chk.returncode == 0, out(chk)
    entry = next(c for c in manifest.load_manifest(root / "model.xlsx")["checks"] if c["id"] == "start_is_month_end")
    sheet, cell = entry["result"].replace("$", "").split("!")
    row = int("".join(ch for ch in cell if ch.isdigit()))
    ws = openpyxl.load_workbook(root / "model.xlsx", data_only=True)[sheet]
    assert isinstance(ws[f"D{row}"].value, dt.datetime) and isinstance(ws[f"E{row}"].value, dt.datetime)
    assert ws[f"G{row}"].value == 0 and not isinstance(ws[f"G{row}"].value, dt.datetime)
    aud = rotm("audit", root, "--strict")
    assert aud.returncode == 0, out(aud)


# ============================================================================= [A-aera-15]

def _sparse(rep: Any) -> list[Any]:
    return [f for f in rep.findings if f.rule == "I-SPARSE-SHEET"]


def test_starter_without_its_font_size_gets_the_sparse_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import dictionary
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    assert dictionary.load_dictionary().rule("sparse_sheet_cells") == 100
    root = copy_model(STARTER, tmp_path / "starter", monkeypatch)
    data = read_spec(root)
    calc = sheet_of(data, "calc")
    assert calc.pop("font_size") == 9
    write_spec(root, data)
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    notes = _sparse(rep)
    assert len(notes) == 1, [f.line() for f in rep.findings]
    f = notes[0]
    assert f.severity == "info" and f.sheet == "calc"
    path = f"sheets[{data['sheets'].index(calc)}]"
    assert f.message.startswith("sheet 'calc' takes size 8 from its role (calc) and holds ")
    assert "populated cells, fewer than rules.sparse_sheet_cells (100); a sheet with white space takes size 9 "            "(Max, spec 1.2)" in f.message, f.message
    assert f.fix == (f'if the sheet has white space, write "font_size": 9 in its entry of model.spec.json ({path}); '
                     "size 8 is for dense sheets"), f.fix
    assert 0 < f.data["populated_cells"] < 100
    assert rep.exit_code == 0                                   # information: the strict exit code is unchanged
    assert manifest.sheet_entry(manifest.load_manifest(root / "model.xlsx"), "calc")["font_size"] == 8


def _shipped_folders() -> list[Path]:
    found = [EXAMPLE, STARTER]
    found += [PLUGIN_TEMPLATES / n for n in SHIPPED_PLUGIN]
    return found


@pytest.mark.parametrize("folder", _shipped_folders(), ids=lambda p: p.name)
def test_shipped_models_get_no_sparse_sheet_note(folder: Path, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """I-3: price_volume_mix gets the note until P7 sets its calc sheet's font_size to 9. The same builds show that
    the valid-range refusal refuses no shipped specification ([A-aera-1])."""
    if not (folder / "model.spec.json").is_file():
        pytest.skip(f"{folder.name} is not in this copy (the skill folder copied alone)")
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = copy_model(folder, tmp_path / folder.name, monkeypatch)
    rep = build.build(root / "model.spec.json", force=True)
    assert rep.exit_code in (0, 1), [f.line() for f in rep.findings]
    assert _sparse(rep) == [], [f.line() for f in _sparse(rep)]


# ============================================================================= supporting [A-aera-1]: valid ranges

def starter_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[dict], None] | None = None) -> Path:
    root = copy_model(STARTER, tmp_path / "starter", monkeypatch)
    if change is not None:
        data = read_spec(root)
        change(data)
        write_spec(root, data)
    return root


def _add_items(*items: dict) -> Callable[[dict], None]:
    def change(d: dict) -> None:
        sheet_of(d, "inputs")["items"].extend(copy.deepcopy(list(items)))
    return change


def test_input_outside_its_valid_range_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter_copy(tmp_path, monkeypatch, _add_items(
        {"id": "k", "label": "K", "value": 5, "type": "number", "valid": {"min": 1000, "max": 1000}, "note": "N"}))
    proc = rotm("build", root, "--force")
    text = out(proc)
    assert proc.returncode == 2, text
    assert "sheets[0].items[2].value: input 'k' (K) holds 5, outside its valid range 1000 to 1000" in text, text
    assert "write a value from 1000 to 1000; the input's note says: N" in text, text
    assert not (root / "model.xlsx").exists()


def test_list_grid_and_list_valid_values_are_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import spec as specmod
    root = starter_copy(tmp_path, monkeypatch, _add_items(
        {"id": "rates", "kind": "list", "label": "Rates", "values": [0.1, 2], "valid": {"min": 0, "max": 1}},
        {"id": "plan", "kind": "grid", "label": "Plan", "rows": {"values": ["A"]}, "cols": {"values": ["x", "y"]},
         "values": [[None, -3]], "valid": {"min": 0}},
        {"id": "mode", "label": "Mode", "value": "fast", "type": "text", "valid": {"list": ["slow", "normal"]}},
        {"id": "ok_mode", "label": "Mode 2", "value": "slow", "type": "text", "valid": {"list": ["slow", "normal"]}},
        {"id": "first", "label": "First day", "value": "2026-01-31", "type": "date", "valid": {"min": 46000}}))
    with pytest.raises(common.Refusal) as exc:
        specmod.load_spec(root)
    lines = exc.value.details
    text = "\n".join(lines)
    assert "sheets[0].items[2].values[1]: input 'rates' (Rates) holds 2, outside its valid range 0 to 1" in text, text
    assert "sheets[0].items[3].values[0][1]: input 'plan' (Plan) holds -3, outside its valid range of at least 0" \
        in text, text
    assert "sheets[0].items[4].value: input 'mode' (Mode) holds 'fast', which is not one of its valid values " \
           "(slow, normal)" in text, text
    assert len(lines) == 3, text                      # values[0] of rates, the null cell, ok_mode and first pass


def test_no_shipped_spec_is_refused_by_its_valid_ranges() -> None:
    from lib import spec as specmod
    for path in _shipped_specs():
        data = common.read_json(path)
        spec = specmod.Spec(path=path, root=path.parent, data=data, name="x", title="x", output=path, product="both",
                            settings={}, sheets=[])
        for si, sheet in enumerate(data["sheets"]):
            if sheet["type"] != "inputs":
                continue
            for ii, item in enumerate(sheet.get("items", [])):
                spec.inputs[item["id"]] = {"kind": item.get("kind", "scalar"), "item": item, "sheet": sheet["name"],
                                           "path": f"sheets[{si}].items[{ii}]"}
        problems = specmod._Problems()
        specmod._check_input_values(spec, problems)
        assert not problems, (path.parent.name, [e.line() for e in problems.items])


# ============================================================================= supporting [A-aera-3, C-sol-5]

def _tolerance_input(value: Any) -> Callable[[dict], None]:
    def change(d: dict) -> None:
        sheet_of(d, "inputs")["items"].append({"id": "tol", "label": "Tolerance", "value": value, "source": "test"})
        check_of(d, "spend_ties_raw")["tolerance"] = "tol"
    return change


@pytest.mark.parametrize("value,shown", [("abc", "'abc'"), (True, "true"), (-1, "-1"), (float("nan"), None),
                                         (float("inf"), None)], ids=["text", "boolean", "negative", "nan", "infinite"])
def test_text_tolerance_is_refused(value: Any, shown: str | None, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """NaN and an infinite value were already refused in v4 by the spec schema (an input value is a finite number);
    text, true/false and a negative number reached the checks sheet before v5."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter_copy(tmp_path, monkeypatch, _tolerance_input(value))
    proc = rotm("build", root, "--force")
    text = out(proc)
    assert proc.returncode == 2, text
    if shown is None:
        assert "sheets[0].items[2].value: " in text and "is not a finite number" in text, text
    else:
        assert (f"sheets[4].checks[0].tolerance: the tolerance of check 'spend_ties_raw' is input 'tol', which holds "
                f"{shown}, not a finite number of 0 or more") in text, text
        assert "write a number of 0 or more in input 'tol'" in text, text
    assert not (root / "model.xlsx").exists()


def test_a_tolerance_of_zero_or_more_builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter_copy(tmp_path, monkeypatch, _tolerance_input(0))
    assert build.build(root / "model.spec.json", force=True).exit_code in (0, 1)


def test_negative_contract_tolerance_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Already refused in v4 by the contract schema (expect.totals[].tolerance is a number of at least 0), as are
    settings.check_tolerance and the tolerance of an accepted record (raw/<t>.controls.json)."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter_copy(tmp_path, monkeypatch)
    cpath = root / "contracts" / "lines.contract.json"
    c = common.read_json(cpath)
    c["expect"]["totals"][0]["tolerance"] = -1
    common.write_json(cpath, c)
    proc = rotm("build", root, "--force")
    assert proc.returncode == 2, out(proc)
    assert "expect.totals[0].tolerance: -1 is out of range" in out(proc), out(proc)
    root2 = starter_copy(tmp_path / "s", monkeypatch, lambda d: d.update(settings={"check_tolerance": -0.5}))
    proc = rotm("build", root2, "--force")
    assert proc.returncode == 2 and "check_tolerance" in out(proc), out(proc)


def test_manifest_records_the_check_and_master_formulas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter_copy(tmp_path, monkeypatch, lambda d: sheet_of(d, "checks")["checks"].append(
        {"id": "neg_cells", "kind": "zero", "severity": "alert", "label": "Negative cells",
         "actual": "COUNTIF({blk:spend},\"<0\")"}))
    _built(root, strict=False)
    data = manifest.load_manifest(root / "model.xlsx")
    book = openpyxl.load_workbook(root / "model.xlsx")
    assert len(data["checks"]) >= 3 and any(c["severity"] == "alert" for c in data["checks"])
    for c in data["checks"]:
        sheet, cell = c["result"].replace("$", "").split("!")
        assert c["result_formula"] == book[sheet][cell].value, c
        assert c["result_formula"].startswith("=--(")
    sheet, cell = data["master_check"].replace("$", "").split("!")
    assert data["master_formula"] == book[sheet][cell].value
    assert data["master_formula"].startswith("=--(OR(SUM(I5:I")


# ============================================================================= supporting [A-aera-16, A-aera-17]

TEXT_LOOKALIKES = ("#N/A", "=x", "0012")


def _text_inputs(d: dict) -> None:
    items = sheet_of(d, "inputs")["items"]
    for k, v in enumerate(TEXT_LOOKALIKES):
        items.append({"id": f"code{k}", "label": f"Code {k}", "value": v, "type": "text", "source": "test"})
    items.append({"id": "codes", "kind": "list", "label": "Codes", "values": list(TEXT_LOOKALIKES), "format": "text"})
    items.append({"id": "code_grid", "kind": "grid", "label": "Code grid", "format": "text",
                  "rows": {"values": ["A"]}, "cols": {"values": ["x", "y", "z"]}, "values": [list(TEXT_LOOKALIKES)]})


def _input_cells(root: Path, *, data_only: bool = False) -> dict[str, list[Any]]:
    import openpyxl
    data = manifest.load_manifest(root / "model.xlsx")
    book = openpyxl.load_workbook(root / "model.xlsx", data_only=data_only)
    found: dict[str, list[Any]] = {}
    for iid in [f"code{k}" for k in range(len(TEXT_LOOKALIKES))] + ["codes", "code_grid"]:
        sheet, rng = data["inputs"][iid].replace("$", "").split("!")
        ws = book[sheet.strip("'")]
        if ":" not in rng:
            found[iid] = [ws[rng]]
        else:
            found[iid] = [c for row in ws[rng] for c in row]
    return found


def test_text_input_that_looks_like_an_error_is_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter_copy(tmp_path, monkeypatch, _text_inputs)
    _built(root, strict=False)
    cells = _input_cells(root)
    for k, v in enumerate(TEXT_LOOKALIKES):
        c = cells[f"code{k}"][0]
        assert (c.value, c.data_type) == (v, "s"), (k, c.value, c.data_type)
    for iid in ("codes", "code_grid"):
        assert [(c.value, c.data_type) for c in cells[iid]] == [(v, "s") for v in TEXT_LOOKALIKES], iid


@pytest.mark.excel
def test_text_input_na_stays_text_after_excel_saves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = starter_copy(tmp_path, monkeypatch, _text_inputs)
    proc = rotm("build", root, "--force")
    assert proc.returncode in (0, 1), out(proc)
    chk = rotm("check", root)                          # recalculates and saves the workbook through Excel
    assert chk.returncode in (0, 1), out(chk)
    cells = _input_cells(root, data_only=True)
    assert all(c.data_type == "s" for cs in cells.values() for c in cs), \
        {k: [(c.value, c.data_type) for c in cs] for k, cs in cells.items()}
    assert cells["code0"][0].value == "#N/A"
    aud = rotm("audit", root, "--strict", "--json", "-")
    rules = [f["rule"] for f in json.loads(aud.stdout)["findings"]]
    assert "V-ERROR" not in rules, rules
    assert aud.returncode == 0, out(aud)


# ============================================================================= supporting [A-aera-2, C-sol-1, C-sol-4] (3)

def _pq_starter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return starter_copy(tmp_path, monkeypatch, lambda d: sheet_of(d, "raw_lines").update(landing="power_query"))


def _change_contract(root: Path, tmp_path: Path) -> None:
    cpath = root / "contracts" / "lines.contract.json"
    c = common.read_json(cpath)
    c["purpose"] = c.get("purpose", "") + " (changed after the query was attached)"
    common.write_json(cpath, c)
    agree_again(root, "lines", tmp_path)


def _workbook_bytes(root: Path) -> tuple[str | None, str | None]:
    return sha(root / "model.xlsx"), sha(root / "model.manifest.json")


def test_first_build_without_a_query_table_keeps_the_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter_copy(tmp_path, monkeypatch)
    _built(root, strict=False)                         # a static workbook: no query table on raw_lines
    data = read_spec(root)
    sheet_of(data, "raw_lines")["landing"] = "power_query"
    write_spec(root, data)
    _change_contract(root, tmp_path)                    # queries/lines.pq.m is now stamped for another contract
    landed = rotm("land", root, "--table", "lines", "--csv", root / "data" / "lines.csv", "--force", "--keep-queries")
    assert landed.returncode in (0, 1), out(landed)
    rep = build.build(root / "model.spec.json", force=True)
    rules = [f.rule for f in rep.findings]
    assert "W-PQ-NOT-ATTACHED" in rules, [f.line() for f in rep.findings]
    assert (root / "model.xlsx").is_file()


def test_rebuild_over_a_query_table_with_a_query_that_cannot_be_attached_is_refused_without_excel(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import check as checkmod
    from test_v4_build import _make_query_table
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _pq_starter(tmp_path, monkeypatch)
    rep = build.build(root / "model.spec.json", force=True)
    assert rep.exit_code in (0, 1)
    _make_query_table(root / "model.xlsx", "tbl_raw_lines", "q_lines")    # as land --power-query leaves it
    assert checkmod.query_tables(root / "model.xlsx") == {"raw_lines": "q_lines"}
    data = manifest.load_manifest(root / "model.xlsx")
    entry = manifest.raw_sheet(data, "lines")
    entry.update(landing="power_query", query_name="q_lines",
                 query_contract_sha256=common.read_json(root / "contracts" / "lines.contract.json")["agreement"]
                 ["contract_sha256"])
    manifest.write_manifest(root / "model.xlsx", data)
    _change_contract(root, tmp_path)
    before = _workbook_bytes(root)
    proc = rotm("build", root, "--strict", "--force")
    text = out(proc)
    assert proc.returncode == 2, text
    assert "raw_lines of model.xlsx is loaded by its Power Query table, and the query cannot be attached again: " \
           "queries/lines.pq.m was written for another contract" in text, text
    assert "nothing was written" in text and f'land "{root.as_posix()}" --table lines' in text, text
    assert _workbook_bytes(root) == before


@pytest.mark.excel
def test_rebuild_with_a_query_that_cannot_be_attached_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import check as checkmod
    root = _pq_starter(tmp_path, monkeypatch)
    proc = rotm("build", root, "--strict", "--force")
    assert proc.returncode == 0, out(proc)
    assert "raw_lines" in checkmod.query_tables(root / "model.xlsx")
    _change_contract(root, tmp_path)
    before = _workbook_bytes(root)
    proc = rotm("build", root, "--strict", "--force")
    text = out(proc)
    assert proc.returncode == 2, text
    assert "raw_lines of model.xlsx is loaded by its Power Query table, and the query cannot be attached again" \
        in text, text
    assert f'land "{root.as_posix()}" --table lines' in text, text
    assert _workbook_bytes(root) == before

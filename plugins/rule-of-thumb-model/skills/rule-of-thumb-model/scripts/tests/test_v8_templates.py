"""Tests of the v8 changes to the shipped specifications and documents (package P2 of the v8 plan). Owner: P2.

Entry [A-aera-3] (major), templates/rolling_forecast: after the monthly roll of the inputs (last_actual,
actual_months, forecast_months) with the landed extract unchanged, a month flagged actual showed 0 revenue and every
check held (v7: check --strict exit 0, 17 of 17 hold, the April actual 0). v8:
- the input closed_months_no_sales (0: closed months that the source confirmed had no sale);
- the items block landed on calc: the landed sales rows of each month, and 1 for an actual month with no landed row;
- the error check closed_months_landed: the count of actual months with no landed row equals the input;
- the README section "The monthly roll": the contract's end date (source.where), the agreement in new words, the
  landing, the inputs, the rebuild and what to read, in this order. The tests run its commands.
Tests (Excel; each on a model made with init --template rolling_forecast outside the plugin folder, its contract
agreed on the task-file channel and the sample landed):
- test_rolling_forecast_shipped_inputs_hold_every_check;
- test_rolling_forecast_roll_without_new_rows_fails_the_landed_months_check (cases 4+8 and 8+4);
- test_rolling_forecast_roll_with_new_rows_holds_and_shows_the_closed_month (the README's commands);
- test_rolling_forecast_closed_month_without_sales_passes_with_the_input_set.
Document tests (no Excel): the README section and its commands; the specification's limitation and note; for each
other shipped folder whose contract scopes its rows by a date or a period, its README (task.md for the worked
example) names that where clause with the input it goes with.

Supporting change [C-sol-10, J-1] part (1): the worked example's alert labels read as a signed threshold
(test_worked_example_alert_labels_read_as_a_signed_threshold, no Excel).

Tests that read PLUGIN/templates skip with a reason when the skill folder is copied alone (v5 plan, I-8). Model
folders lie in outside_tmp (conftest.py), so the tests pass wherever --basetemp points.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib import common, manifest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
NO_PLUGIN = "the skill folder was copied without its plugin, so PLUGIN/templates is absent"
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason=NO_PLUGIN)
TEMPLATES = PLUGIN / "templates"
RF = TEMPLATES / "rolling_forecast"
EXAMPLE = SKILL / "examples" / "sales_vs_target"
STARTER = SKILL / "templates" / "starter"
TEXTS_SECTION = "## Texts that follow an input"
ROLL_SECTION = "## The monthly roll"
REPLY = "Agreed as proposed."
NEW_REPLY = "Agreed: the extract now ends on 2026-04-30."
SEED = 20261126                                          # the April rows of the roll test


def rotm(*args: object, env: dict[str, str] | None = None, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


def out(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def ok(proc: subprocess.CompletedProcess[str], what: Any) -> subprocess.CompletedProcess[str]:
    assert proc.returncode == 0, (what, out(proc)[-3000:])
    return proc


def flat(text: str) -> str:
    return " ".join(text.split())


def environ(*, excel: bool) -> dict[str, str]:
    env = dict(os.environ, PYTHONUTF8="1")
    if not excel:
        env["ROTM_NO_EXCEL"] = "1"
    return env


def spec_of(folder: Path) -> dict[str, Any]:
    return common.read_json(folder / "model.spec.json")


def sheet_of(spec: dict[str, Any], sheet_type: str) -> dict[str, Any]:
    return next(s for s in spec["sheets"] if s["type"] == sheet_type)


def input_of(spec: dict[str, Any], input_id: str) -> dict[str, Any]:
    return next(i for i in sheet_of(spec, "inputs")["items"] if i["id"] == input_id)


def check_of(spec: dict[str, Any], check_id: str) -> dict[str, Any]:
    return next(c for c in sheet_of(spec, "checks")["checks"] if c["id"] == check_id)


def block_of(spec: dict[str, Any], block_id: str) -> dict[str, Any]:
    return next(b for s in spec["sheets"] for b in s.get("blocks", []) if b["id"] == block_id)


def section(text: str, heading: str) -> str:
    assert f"\n{heading}\n" in text, heading
    return text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


# ============================================================================= [A-aera-3] the model and the roll

def rf_model(root: Path, env: dict[str, str]) -> Path:
    """A model made from the template as a user makes it: init --template rolling_forecast (the contract back to
    proposed), the contract agreed on the task-file channel, the sample extract landed."""
    task = root.parent / f"{root.name}_task.md"
    task.write_text(f"The user's reply to the contract: {REPLY}\n", encoding="utf-8")
    for args in (("init", root, "--template", "rolling_forecast", "--name", f"v8_p2_{root.name}"),
                 ("contract", "agree", root, "--table", "sales", "--reply", REPLY, "--by", "tester",
                  "--channel", "task-file", "--source-file", task),
                 ("land", root, "--table", "sales", "--csv", root / "data" / "sales.csv")):
        ok(rotm(*args, env=env), args[:2])
    return root


# (last_actual, actual_months, forecast_months) after closing April (4+8) and August (8+4)
ROLLS = {"4+8": ("2026-04-30", 4, 8), "8+4": ("2026-08-31", 8, 4)}


def roll(root: Path, last_actual: str, actual: int, forecast: int, *, closed_no_sales: int | None = None,
         texts: bool = False) -> None:
    """The inputs of README "The monthly roll" step 4, in model.spec.json; with `texts` also the two texts of
    "Texts that follow an input" (the title and the purpose of calc)."""
    spec = spec_of(root)
    input_of(spec, "last_actual")["value"] = last_actual
    input_of(spec, "actual_months")["value"] = actual
    input_of(spec, "forecast_months")["value"] = forecast
    if closed_no_sales is not None:
        input_of(spec, "closed_months_no_sales")["value"] = closed_no_sales
    if texts:
        spec["title"] = spec["title"].replace("3+9", f"{actual}+{forecast}")
        calc = next(s for s in spec["sheets"] if s["name"] == "calc")
        calc["purpose"] = calc["purpose"].replace("3+9", f"{actual}+{forecast}")
    common.write_json(root / "model.spec.json", spec)


def build_and_check(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    ok(rotm("build", root, "--strict", "--force", env=env), "build --strict --force")
    return rotm("check", root, "--strict", env=env)


def report_of(root: Path) -> dict[str, Any]:
    return json.loads((root / "reports" / "check.json").read_text(encoding="utf-8"))


def error_states(root: Path) -> dict[str, str]:
    """The state of each error check after rotm.py check (reports/check.json, data.checks)."""
    return {c["id"]: c["state"] for c in report_of(root)["data"]["checks"] if c.get("severity", "error") == "error"}


def month_ends(last: dt.date) -> list[dt.date]:
    """The month ends of 2026 up to `last` (the months flagged actual)."""
    found = []
    for m in range(1, 13):
        end = (dt.date(2026, m + 1, 1) if m < 12 else dt.date(2027, 1, 1)) - dt.timedelta(days=1)
        if end <= last:
            found.append(end)
    return found


def landed_months(root: Path) -> set[dt.date]:
    """The month ends of the sale dates in raw/sales.csv."""
    with (root / "raw" / "sales.csv").open(encoding="utf-8", newline="") as f:
        dates = [dt.date.fromisoformat(r["sale_date"]) for r in csv.DictReader(f)]
    return {month_ends(dt.date(d.year, 12, 31))[d.month - 1] for d in dates}


@needs_plugin
@pytest.mark.excel
def test_rolling_forecast_shipped_inputs_hold_every_check(outside_tmp: Path) -> None:
    """The shipped inputs (3 actual months, the sample landed): build --strict and check --strict exit 0; every
    error check holds, closed_months_landed among them; 18 error checks (8 generated, 10 written in the
    specification) and the master check 0."""
    env = environ(excel=True)
    root = rf_model(outside_tmp / "rf", env)
    checked = build_and_check(root, env)
    assert checked.returncode == 0, out(checked)[-3000:]
    states = error_states(root)
    assert states.get("closed_months_landed") == "pass", states
    assert set(states.values()) == {"pass"}, states
    assert len(states) == 18, sorted(states)
    report = report_of(root)
    assert report["data"]["master"]["state"] == "pass", report["data"]["master"]
    assert "checks: 18 of 18 hold" in report["notes"], [n for n in report["notes"] if n.startswith("checks:")]


@needs_plugin
@pytest.mark.excel
@pytest.mark.parametrize("case", list(ROLLS), ids=list(ROLLS))
def test_rolling_forecast_roll_without_new_rows_fails_the_landed_months_check(case: str, outside_tmp: Path) -> None:
    """The three inputs rolled and the landed rows unchanged (the extract still ends on 2026-03-31): check --strict
    exits 1, the failing error checks are exactly {closed_months_landed}, its actual is the number of actual months
    with no landed row (1 for 4+8, 5 for 8+4) against the expected 0, and the master check fails."""
    env = environ(excel=True)
    root = rf_model(outside_tmp / "rf", env)
    last, actual, forecast = ROLLS[case]
    roll(root, last, actual, forecast)
    checked = build_and_check(root, env)
    assert checked.returncode == 1, out(checked)[-3000:]
    failed = {k for k, v in error_states(root).items() if v != "pass"}
    assert failed == {"closed_months_landed"}, failed
    report = report_of(root)
    entry = next(c for c in report["data"]["checks"] if c["id"] == "closed_months_landed")
    empty = [m for m in month_ends(dt.date.fromisoformat(last)) if m not in landed_months(root)]
    assert len(empty) == {"4+8": 1, "8+4": 5}[case], empty
    assert (entry["actual"], entry["expected"]) == (len(empty), 0), entry
    assert report["data"]["master"]["state"] == "fail", report["data"]["master"]


# ----------------------------------------------------------------------------- the README's commands

# The commands of README "The monthly roll" as the README writes them (test_rolling_forecast_readme_gives_the_monthly
# _roll_in_order finds each one there, in this order); the roll test runs the static path with the placeholders
# filled in.
M = '"<model folder>"'
PROPOSE = (f'rotm.py contract propose {M} --table sales --force --csv "<model folder>/raw/sales.csv" --key line_id '
           '--grain "one row per sales line" --source "proj.sales.lines s" --connector power_query --system BigQuery '
           "--where \"s.sale_date BETWEEN '2026-01-01' AND '<new closing date>'\" --unit revenue=KRW "
           '--type revenue=number --rows 1:100000 --total revenue=307355000 '
           '--purpose "Sales lines of the closed months for the rolling forecast"')
SHOW = f"rotm.py contract show {M} --table sales"
AGREE = f'rotm.py contract agree {M} --table sales --reply "<the user\'s new words>" --by <name>'
QUERIES = f"rotm.py land {M} --table sales"
LAND_CSV = f'rotm.py land {M} --table sales --csv "<export.csv>" --force --control sales.revenue=<source total>'
PQ_ATTACH = f"rotm.py land {M} --table sales --power-query"
PQ_REFRESH = f"rotm.py check {M} --refresh --strict"
PQ_EXTEND = f"rotm.py extend {M} --table sales"
PQ_ACCEPT = f"rotm.py check {M} --accept-refresh --control sales.revenue=<source total> --strict"
BUILD = f"rotm.py build {M} --strict --force"
CHECK = f"rotm.py check {M} --strict"
RENDER = 'rotm.py render "<model folder>/model.xlsx"'
ROLL_COMMANDS = [PROPOSE, SHOW, AGREE, QUERIES, LAND_CSV, PQ_ATTACH, PQ_REFRESH, PQ_EXTEND, PQ_ACCEPT, BUILD, CHECK,
                 RENDER]

# README step 1: the edits of the proposed contracts/sales.contract.json that give the agreed values back
# (column -> (source_expression, description)); propose writes the sample's header as the source expression, no
# description, no source.object when --source holds a space, and a display format with its note for revenue.
AGREED_COLUMNS = {"line_id": ("s.line_id", "sales line id"), "sale_date": ("s.sale_date", "date of the sale"),
                  "product": ("s.product_name", "product"), "revenue": ("s.net_revenue_krw", "net revenue in KRW")}
SOURCE_OBJECT = "proj.sales.lines"
FORMAT_NOTE = "display formats proposed from the sample"


def restore_agreed_values(path: Path) -> None:
    """README "The monthly roll" step 1: the edits of the proposed contract (a proposed contract may be edited)."""
    data = common.read_json(path)
    assert data["agreement"]["status"] == "proposed", data["agreement"]
    data["source"]["object"] = SOURCE_OBJECT
    for col in data["columns"]:
        col["source_expression"], col["description"] = AGREED_COLUMNS[col["name"]]
        if col["name"] == "revenue":
            col.pop("format", None)
    data["notes"] = [n for n in data["notes"] if not n.startswith(FORMAT_NOTE)]
    common.write_json(path, data)


def fill(command: str, values: dict[str, str]) -> list[str]:
    """A README command with its placeholders filled in, split as a shell splits it; 'rotm.py' left out."""
    for key, value in values.items():
        command = command.replace(key, value)
    assert "<" not in command, command
    parts = shlex.split(command)
    assert parts[0] == "rotm.py", parts
    return parts[1:]


def export_with_april(root: Path, folder: Path) -> tuple[Path, float, int]:
    """The export of the new query: the landed rows plus 30 sales lines of April 2026 (fixed seed; every product).
    Returns (file, April revenue in KRW, revenue total in KRW)."""
    with (root / "raw" / "sales.csv").open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    rng = random.Random(SEED)
    products = ["Alpha", "Beta", "Gamma", "Delta"]
    april = [{"line_id": f"S{len(rows) + i + 1:05d}", "sale_date": f"2026-04-{rng.randint(1, 30):02d}",
              "product": products[i % 4], "revenue": str(rng.randint(2, 400) * 5000)} for i in range(30)]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "export_sales.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows + april)
    total = sum(int(float(r["revenue"])) for r in rows + april)
    return path, float(sum(int(r["revenue"]) for r in april)), total


def month_total(workbook: Path, month_end: dt.date) -> Any:
    """The value Excel saved in the total row of the block actual (calc) under the month's header."""
    import openpyxl
    from openpyxl.utils import column_index_from_string, get_column_letter
    man = manifest.load_manifest(workbook)
    blk = next(b for s in man["sheets"] if s["name"] == "calc" for b in s.get("blocks", []) if b["id"] == "actual")
    first, last = (re.match(r"[A-Z]+", c).group(0) for c in blk["body"].split(":"))   # type: ignore[union-attr]
    wb = openpyxl.load_workbook(workbook, data_only=True, read_only=True)
    try:
        ws = wb["calc"]
        for ci in range(column_index_from_string(first), column_index_from_string(last) + 1):
            col = get_column_letter(ci)
            head = ws[f"{col}{blk['header_row']}"].value
            if isinstance(head, (int, float)) and not isinstance(head, bool):
                head = dt.datetime(1899, 12, 30) + dt.timedelta(days=float(head))
            if isinstance(head, dt.datetime) and head.date() == month_end:
                return ws[f"{col}{blk['total_row']}"].value
    finally:
        wb.close()
    raise AssertionError(f"no column of the block actual is headed {month_end}")


@needs_plugin
@pytest.mark.excel
def test_rolling_forecast_roll_with_new_rows_holds_and_shows_the_closed_month(outside_tmp: Path) -> None:
    """The roll as README "The monthly roll" gives it, static path: the contract proposed again with the new end
    date and the agreed values restored (contract show lists source.where as the only change against version 1),
    agreed in new words (task-file channel), the query text written again (its WHERE ends on 2026-04-30), the export
    with April rows landed with --force and --control, the inputs and the two texts rolled to 4+8: build --strict and
    check --strict exit 0, every error check holds, and the April actual total on calc equals the April revenue of
    the export divided by 1000 (relative difference at most 1e-9)."""
    env = environ(excel=True)
    root = rf_model(outside_tmp / "rf", env)
    export, april_sum, total = export_with_april(root, outside_tmp / "source")
    task = outside_tmp / "rf_task_v2.md"
    task.write_text(f"The user's reply to contract version 2: {NEW_REPLY}\n", encoding="utf-8")
    values = {"<model folder>": root.as_posix(), "<new closing date>": "2026-04-30", "<export.csv>": export.as_posix(),
              "<source total>": str(total), "<the user's new words>": NEW_REPLY, "<name>": "tester"}
    cpath = root / "contracts" / "sales.contract.json"
    ok(rotm(*fill(PROPOSE, values), env=env), "contract propose --force")
    restore_agreed_values(cpath)
    ok(rotm(*fill(SHOW, values), env=env), "contract show")
    md = (root / "contracts" / "sales.contract.md").read_text(encoding="utf-8")
    listed: list[str] = []
    for line in md.split("\nChanges since version 1", 1)[1].splitlines()[1:]:
        if line.startswith("- "):
            listed.append(line)
        elif listed and not line.strip():
            break
    assert len(listed) == 1 and listed[0].startswith("- source.where:") and "'2026-04-30'" in listed[0], listed
    ok(rotm(*fill(AGREE, values), "--channel", "task-file", "--source-file", task, env=env), "contract agree")
    shipped = common.read_json(RF / "contracts" / "sales.contract.json")
    new = common.read_json(cpath)
    assert new["source"]["where"] == "s.sale_date BETWEEN '2026-01-01' AND '2026-04-30'", new["source"]
    for col in new["columns"]:
        col.pop("csv_header", None)
    left_out = ("agreement", "version", "proposed_at")
    assert ({k: v for k, v in new.items() if k not in left_out} | {"source": dict(new["source"], where=None)}
            == {k: v for k, v in shipped.items() if k not in left_out} | {"source": dict(shipped["source"], where=None)})
    ok(rotm(*fill(QUERIES, values), env=env), "land (query texts)")
    sql = (root / "queries" / "sales.sql").read_text(encoding="utf-8")
    assert "WHERE s.sale_date BETWEEN '2026-01-01' AND '2026-04-30'" in sql, sql
    ok(rotm(*fill(LAND_CSV, values), env=env), "land --csv --force --control")
    roll(root, "2026-04-30", 4, 8, texts=True)
    ok(rotm(*fill(BUILD, values), env=env), "build --strict --force")
    checked = rotm(*fill(CHECK, values), env=env)
    assert checked.returncode == 0, out(checked)[-3000:]
    states = error_states(root)
    assert states.get("closed_months_landed") == "pass" and set(states.values()) == {"pass"}, states
    april = month_total(root / "model.xlsx", dt.date(2026, 4, 30))
    want = april_sum / 1000
    assert want > 0 and isinstance(april, (int, float)), april
    assert abs(april - want) <= 1e-9 * abs(want), (april, want)


@needs_plugin
@pytest.mark.excel
def test_rolling_forecast_closed_month_without_sales_passes_with_the_input_set(outside_tmp: Path) -> None:
    """A closed month that had no sale: the landed rows unchanged, the inputs rolled to 4+8 and
    closed_months_no_sales set to 1 (README: only after the source confirmed it): check --strict exits 0 and every
    error check holds."""
    env = environ(excel=True)
    root = rf_model(outside_tmp / "rf", env)
    roll(root, "2026-04-30", 4, 8, closed_no_sales=1)
    checked = build_and_check(root, env)
    assert checked.returncode == 0, out(checked)[-3000:]
    states = error_states(root)
    assert states.get("closed_months_landed") == "pass" and set(states.values()) == {"pass"}, states


# ----------------------------------------------------------------------------- documents and the specification

def _parses(command: str) -> None:
    """The command's options are options of its rotm.py subcommand (the parser rotm.py builds), with sample values
    in place of the placeholders."""
    import rotm as rotm_cli                              # scripts/ is on sys.path (conftest.py)
    parts = fill(command, {"<model folder>": "m", "<new closing date>": "2026-04-30", "<export.csv>": "e.csv",
                           "<source total>": "1", "<the user's new words>": "agreed", "<name>": "n"})
    module_name, prefix, text, _owner = rotm_cli.COMMANDS[parts[0]]
    parser = argparse.ArgumentParser(prog=f"rotm.py {parts[0]}", description=text)
    getattr(importlib.import_module(f"lib.{module_name}"), f"add_{prefix}_arguments")(parser)
    try:
        parser.parse_args(parts[1:])
    except SystemExit as exc:                            # argparse exits 2 on an unknown option
        raise AssertionError(f"{command}: rotm.py {parts[0]} refuses its options") from exc


@needs_plugin
def test_rolling_forecast_readme_gives_the_monthly_roll_in_order() -> None:
    """README section "The monthly roll" (before "Limitations"): its commands in the entry's order (the contract
    proposed again with the new end date, shown, agreed in the user's new words; the query text; the static landing;
    the Power Query landing; the rebuild, check and render), each accepted by rotm.py's parser; the edits of the
    proposed contract the roll test makes; the inputs and the two texts; what to read; what the check shows when the
    extract was not moved; when to raise the input. The bullet 'After closing a month' is gone."""
    raw = (RF / "README.md").read_text(encoding="utf-8")
    assert raw.index(f"\n{ROLL_SECTION}\n") < raw.index("\n## Limitations\n")
    text = section(raw, ROLL_SECTION)
    steps = re.findall(r"^(\d+)\. ", text, flags=re.M)
    assert steps == [str(i) for i in range(1, len(steps) + 1)] and len(steps) >= 6, steps
    at = -1
    for command in ROLL_COMMANDS:
        assert command in text, command
        assert text.index(command) > at, command
        at = text.index(command)
        _parses(command)
    body = flat(text)
    order = ["source.where", PROPOSE, SHOW, "new words", AGREE, QUERIES, LAND_CSV, PQ_ATTACH, PQ_ACCEPT,
             "`last_actual`", "`actual_months`", "`forecast_months`", "Texts that follow an input", BUILD, CHECK, RENDER,
             "`closed_months_landed`", "the source's total of that month", "not moved", "`closed_months_no_sales`"]
    at = -1
    for item in order:
        assert body.find(flat(item), at + 1) > at, item
        at = body.find(flat(item), at + 1)
    for name, (expr, desc) in AGREED_COLUMNS.items():
        assert f"`{name}`" in body and f"`{expr}`" in body and f"`{desc}`" in body, name
    assert f"`{SOURCE_OBJECT}`" in body and "`format`" in body and "--rewrite-queries" in body
    assert "FAIL" in body and "actual 1, expected 0" in body and "master check" in body
    assert "After closing a month" not in raw
    assert "in `model.spec.json` (the items of the inputs sheet)" in flat(raw)        # test_v3_docs.py keeps it
    texts = flat(section(raw, TEXTS_SECTION))
    assert "The monthly roll" in texts, texts


@needs_plugin
def test_rolling_forecast_spec_names_the_roll_in_its_limitation_and_note() -> None:
    """The specification: the input, the block and the check of plan 5.2; limitations[2] (shown on the cover) says
    that the roll moves the end date of source.where and points to the README section; the note that cites the
    contract's source where says that the extract ends at that date and that the roll moves it."""
    spec = spec_of(RF)
    inp = input_of(spec, "closed_months_no_sales")
    assert (inp["label"], inp["value"], inp["type"], inp["unit"], inp["valid"]) == \
        ("Closed months with no sales, confirmed with the source", 0, "integer", "months", {"min": 0, "max": 12}), inp
    assert inp["source"] == "source system: months the source confirms had no sale", inp
    assert "closed_months_landed" in inp["note"] and "The monthly roll" in inp["note"], inp
    blk = block_of(spec, "landed")
    assert blk["cols"] == {"same_as": "actual"}, blk
    items = {i["id"]: i for i in blk["items"]}
    assert items["rows"]["expr"] == "COUNTIFS({h:sales.month_end},{col})", items
    assert items["empty"]["expr"] == "{b:is_actual.flag}*--({r:rows}=0)", items
    assert [i["id"] for i in block_of(spec, "is_actual")["items"]] == ["flag"]
    chk = check_of(spec, "closed_months_landed")
    assert (chk["kind"], chk.get("severity", "error"), chk["actual"], chk["expected"]) == \
        ("equal", "error", "SUM({blk:landed.empty})", "{in:closed_months_no_sales}"), chk
    for e in [i["expr"] for i in blk["items"]] + [chk["actual"], chk["expected"]]:
        assert not re.search(r"IFERROR|IFNA|ROUND|\bINT\(|TRUNC|CEILING|FLOOR", e), e
    lim = spec["limitations"][2]
    assert "source.where" in lim and 'README "The monthly roll"' in lim and "closed_months_landed" in lim, lim
    assert "on the inputs sheet, rebuild" not in lim, lim
    note = next(n for n in sheet_of(spec, "notes")["notes"] if n["source"] == "contract sales, source where")
    assert "source.where" in note["text"] and "The monthly roll" in note["text"], note


@needs_plugin
def test_rolling_forecast_check_count_is_stated_as_measured() -> None:
    """templates/README.md (the rolling_forecast row) and the template's README state 18 of 18 (measured in Excel by
    test_rolling_forecast_shipped_inputs_hold_every_check)."""
    lib = (TEMPLATES / "README.md").read_text(encoding="utf-8")
    row = next(line for line in lib.splitlines() if line.startswith("| `rolling_forecast` |"))
    assert row.endswith("| 18 of 18 hold |"), row
    readme = flat((RF / "README.md").read_text(encoding="utf-8"))
    assert "checks: 18 of 18 hold" in readme and "17 of 17" not in readme


@needs_plugin
def test_rolling_forecast_builds_strict_without_text_width_notes(outside_tmp: Path) -> None:
    """No Excel: a model made from the template builds with --strict (exit 0: no warning such as W-CHECK-GUARD) and
    build reports I-TEXT-WIDTH 0 times, also after the roll to 4+8 with the two texts."""
    env = environ(excel=False)
    root = rf_model(outside_tmp / "rf", env)
    for step in ("shipped", "rolled"):
        if step == "rolled":
            roll(root, "2026-04-30", 4, 8, texts=True)
        built = rotm("build", root, "--strict", "--force", env=env)
        assert built.returncode == 0, (step, out(built)[-3000:])
        assert "I-TEXT-WIDTH" not in out(built), (step, out(built)[-3000:])


# ----------------------------------------------------------------------------- part (3): the other folders

STEPS = ["contract propose", "--force", "contract show", "new words", "contract agree", "land", "rebuild",
         "When the contract must change after agreement"]


def _names_the_where_clause(folder: Path, doc: str, inputs: tuple[str, ...]) -> None:
    """The section "Texts that follow an input" of the folder's document holds each contract's source.where text
    exactly (from the contract JSON), the input ids it goes with and the steps of a contract change."""
    text = flat(section((folder / doc).read_text(encoding="utf-8"), TEXTS_SECTION))
    contracts = sorted((folder / "contracts").glob("*.contract.json"))
    assert contracts, folder
    for path in contracts:
        where = common.read_json(path)["source"]["where"]
        assert where and f"`{where}`" in text, (path.name, where)
    for name in inputs:
        assert f"`{name}`" in text, name
        input_of(spec_of(folder), name)                  # the input exists
    for step in STEPS:
        assert step in text, step


def test_starter_names_the_where_clause_with_its_input() -> None:
    _names_the_where_clause(STARTER, "README.md", ("start_month",))


def test_sales_vs_target_names_the_where_clause_with_its_input() -> None:
    _names_the_where_clause(EXAMPLE, "task.md", ("start_month",))


@needs_plugin
def test_ar_aging_names_the_where_clause_with_its_input() -> None:
    _names_the_where_clause(TEMPLATES / "ar_aging", "README.md", ("as_of",))


@needs_plugin
def test_budget_vs_actual_names_the_where_clause_with_its_input() -> None:
    _names_the_where_clause(TEMPLATES / "budget_vs_actual", "README.md", ("start_month",))


@needs_plugin
def test_price_volume_mix_names_the_where_clause_with_its_input() -> None:
    _names_the_where_clause(TEMPLATES / "price_volume_mix", "README.md", ("py_label", "cy_label"))


# ============================================================================= [C-sol-10, J-1] part (1)

ALERT_INPUT_LABEL = "Alert when monthly variance % is below"
ALERT_LABEL = "Region-months whose variance % is below the alert input"
OLD_LABELS = ("Alert when a region-month is below target by more than",
              "Region-months below target by more than the alert input")


def test_worked_example_alert_labels_read_as_a_signed_threshold(tmp_path: Path) -> None:
    """The worked example: the input alert_below (-0.15) and the alert far_below have the labels of plan 5.2; no
    file of the example quotes the old labels; a copy builds with --strict (no Excel) and build reports
    I-TEXT-WIDTH 0 times."""
    spec = spec_of(EXAMPLE)
    inp = input_of(spec, "alert_below")
    assert (inp["label"], inp["value"]) == (ALERT_INPUT_LABEL, -0.15), inp
    alert = check_of(spec, "far_below")
    assert alert["severity"] == "alert" and alert["label"] == ALERT_LABEL, alert
    for name in ("model.spec.json", "task.md"):
        text = (EXAMPLE / name).read_text(encoding="utf-8")
        assert not any(old in text for old in OLD_LABELS), name
    dest = tmp_path / EXAMPLE.name
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("__pycache__", "reports", "lift", "*.xlsx",
                                                                "*.manifest.json", "*.bak_*"))
    env = dict(environ(excel=False), ROTM_EXAMPLE_ROOTS=str(tmp_path))
    built = rotm("build", dest, "--strict", "--force", env=env)
    assert built.returncode == 0, out(built)[-3000:]
    assert "I-TEXT-WIDTH" not in out(built), out(built)[-3000:]

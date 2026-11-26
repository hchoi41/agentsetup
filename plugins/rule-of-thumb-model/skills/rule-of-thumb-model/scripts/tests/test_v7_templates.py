"""Tests of the v7 changes to the shipped specifications (package P2 of the v7 plan). Owner: P2.

Entry [C-sol-3] (major), templates/ar_aging: the summary column "Over 90 days" read the last bucket of the aging grid
and the check over90_ties read the last bucket number, so with the last bucket changed (as the README allows) the
column showed another amount under the same label and every check held. v7: the input over90_days (fixed at 90)
and the helper column over90_k give the measure; over90_ties compares it with SUMIFS over the days past due. "Not
due" read the first bucket; v7: the helper column not_due_k (0 or fewer days past due), and pct_ties reads the same
definition by another path. Tests:
- test_ar_aging_over90_follows_the_cutoff_when_the_last_bucket_changes (Excel): two bucket lists the README allows;
- test_ar_aging_shipped_lists_give_the_shipped_over90 (Excel);
- test_ar_aging_cutoff_input_is_fixed_at_90;
- the three other inputs of the v6 change note (ar_aging as_of, price_volume_mix py_label and cy_label,
  rolling_forecast actual_months and forecast_months): each template README lists every text to change with the
  input (section "Texts that follow an input"), one document test per case.

Supporting change [A-aera-4, A-aera-13] part (2): a check of each cell of a ratio grid. The worked example gets the
block pct_cell_diff and the check pct_cell_ties (the percentage grid), and the items block var_pct_cell_diff and the
check var_pct_cell_ties (the item var_pct of the block bridge); rolling_forecast gets the block runrate_cell_diff and
the check runrate_cell_ties (the run-rate grid). The summed-product checks pct_grid_ties, var_pct_all and
runrate_ties do not find a grid in which every cell holds the overall rate. Ratio columns of measures blocks cannot
be addressed by a check (spec.py: {blk:<block>.<id>} names a line item of an items block only), so they get none.
The one-expression form SUMPRODUCT(ABS(...)) was tried for var_pct and left: the script of rotm.py lift computes
#VALUE! for ABS over a range, so lift --run did not tie out; the block form ties out.

Tests that read PLUGIN/templates skip with a reason when the skill folder is copied alone (v5 plan, I-8). Build
copies lie in outside_tmp (conftest.py), so the tests pass wherever --basetemp points.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
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
AR = TEMPLATES / "ar_aging"
PVM = TEMPLATES / "price_volume_mix"
RF = TEMPLATES / "rolling_forecast"
EXAMPLE = SKILL / "examples" / "sales_vs_target"
LENGTH_LIMIT = 100                                       # characters (rules of the style dictionary)
TEXTS_SECTION = "## Texts that follow an input"


def rotm(*args: object, env: dict[str, str] | None = None, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


def out(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def flat(text: str) -> str:
    return " ".join(text.split())


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


def copy_model(folder: Path, root: Path, *, excel: bool) -> tuple[Path, dict[str, str]]:
    """A copy of a shipped folder (without its workbook, manifest and reports) under root that keeps the shipped
    agreement: channel example is accepted under root (ROTM_EXAMPLE_ROOTS, as test_v6_templates.py does)."""
    dest = root / folder.name
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "reports", "lift", "*.xlsx",
                                                                "*.manifest.json", "*.bak_*"))
    env = dict(os.environ, ROTM_EXAMPLE_ROOTS=str(root), PYTHONUTF8="1")
    if not excel:
        env["ROTM_NO_EXCEL"] = "1"
    return dest, env


def build_and_check(dest: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str],
                                                              subprocess.CompletedProcess[str]]:
    built = rotm("build", dest, "--strict", "--force", env=env)
    assert built.returncode == 0, out(built)[-3000:]
    return built, rotm("check", dest, "--strict", env=env)


def check_states(dest: Path, *, severity: str = "error") -> dict[str, str]:
    """The state of each check of that severity after rotm.py check (reports/check.json, data.checks)."""
    report = json.loads((dest / "reports" / "check.json").read_text(encoding="utf-8"))
    return {c["id"]: c["state"] for c in report["data"]["checks"] if c.get("severity", "error") == severity}


def cached(workbook: Path, refs: list[str]) -> list[Any]:
    """The values Excel saved in the workbook at sheet!cell references."""
    import openpyxl
    wb = openpyxl.load_workbook(workbook, data_only=True, read_only=True)
    try:
        return [wb[r.split("!")[0]][r.split("!")[1].replace("$", "")].value for r in refs]
    finally:
        wb.close()


def measure_total_ref(workbook: Path, block_id: str, measure_id: str) -> tuple[str, str]:
    """(total-row cell, header text) of a measure of a measures block, from the manifest."""
    man = manifest.load_manifest(workbook)
    for s in man["sheets"]:
        for b in s.get("blocks", []):
            if b["id"] == block_id:
                m = next(m for m in b["measures"] if m["id"] == measure_id)
                return f"{s['name']}!{m['col']}{b['total_row']}", m["label"]
    raise KeyError(block_id)


# ============================================================================= [C-sol-3] ar_aging

def _days_and_amounts(dest: Path) -> list[tuple[int, float]]:
    """(days past due, open amount in KRW) of each landed invoice, from raw/open_items.csv and the as-of input."""
    as_of = dt.date.fromisoformat(input_of(spec_of(dest), "as_of")["value"])
    with (dest / "raw" / "open_items.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows
    return [((as_of - dt.date.fromisoformat(r["due_date"])).days, float(r["open_amount"])) for r in rows]


def _set_buckets(dest: Path, bounds: list[int], names: list[str]) -> None:
    spec = spec_of(dest)
    input_of(spec, "bucket_from")["values"] = bounds
    input_of(spec, "bucket_names")["values"] = names
    common.write_json(dest / "model.spec.json", spec)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(abs(a), abs(b), 1e-9)


# Bucket lists the README allows (bounds ascending, names paired by position, the first bound below every invoice):
# - last_121: the last bucket starts at 121 and is named "Over 120 days" (the cold reviewer's case: v6 showed
#   4,638 KRW thousand under "Over 90 days", where the CSV gives 31,009);
# - four_buckets: the first bucket holds the invoices up to 30 days past due (v6 showed them under "Not due").
BUCKET_LISTS = {
    "last_121": ([-36500, 1, 31, 61, 121], ["Not due", "1 to 30 days", "31 to 60 days", "61 to 120 days",
                                            "Over 120 days"]),
    "four_buckets": ([-36500, 31, 61, 121], ["Up to 30 days", "31 to 60 days", "61 to 120 days", "Over 120 days"]),
}


@needs_plugin
@pytest.mark.excel
@pytest.mark.parametrize("case", list(BUCKET_LISTS), ids=list(BUCKET_LISTS))
def test_ar_aging_over90_follows_the_cutoff_when_the_last_bucket_changes(case: str, outside_tmp: Path) -> None:
    """A copy of ar_aging with another bucket list: build --strict and check --strict (Excel) exit 0; the summary
    total under "Over 90 days" equals the open amount of the invoices more than 90 days past due that this test
    computes from raw/open_items.csv (divided by 1000), and the total under "Not due" equals the open amount of the
    invoices 0 or fewer days past due; over90_ties and pct_ties hold."""
    dest, env = copy_model(AR, outside_tmp, excel=True)
    _set_buckets(dest, *BUCKET_LISTS[case])
    _built, checked = build_and_check(dest, env)
    assert checked.returncode == 0, out(checked)[-3000:]
    states = check_states(dest)
    assert states["over90_ties"] == "pass" and states["pct_ties"] == "pass", states
    wb = dest / "model.xlsx"
    over_ref, over_label = measure_total_ref(wb, "overdue", "over90")
    nd_ref, nd_label = measure_total_ref(wb, "overdue", "not_due")
    assert (over_label, nd_label) == ("Over 90 days", "Not due")
    over, not_due = cached(wb, [over_ref, nd_ref])
    rows = _days_and_amounts(dest)
    want_over = sum(a for d, a in rows if d > 90) / 1000
    want_nd = sum(a for d, a in rows if d <= 0) / 1000
    assert want_over > 0 and want_nd > 0
    assert _close(over, want_over), (case, over_ref, over, want_over)
    assert _close(not_due, want_nd), (case, nd_ref, not_due, want_nd)


@needs_plugin
@pytest.mark.excel
def test_ar_aging_shipped_lists_give_the_shipped_over90(outside_tmp: Path) -> None:
    """With the shipped bucket lists, the over-90 and not-due totals after build and check (Excel) equal the values
    Excel saved in the shipped model.xlsx (read from a copy), and the CSV calculation of the last and the first
    bucket."""
    shipped = outside_tmp / "shipped_model.xlsx"
    shutil.copy2(AR / "model.xlsx", shipped)
    shutil.copy2(AR / "model.manifest.json", outside_tmp / "shipped_model.manifest.json")
    ship_over_ref, _ = measure_total_ref(shipped, "overdue", "over90")
    ship_nd_ref, _ = measure_total_ref(shipped, "overdue", "not_due")
    ship_over, ship_nd = cached(shipped, [ship_over_ref, ship_nd_ref])
    dest, env = copy_model(AR, outside_tmp / "m", excel=True)
    _built, checked = build_and_check(dest, env)
    assert checked.returncode == 0, out(checked)[-3000:]
    wb = dest / "model.xlsx"
    over_ref, _ = measure_total_ref(wb, "overdue", "over90")
    nd_ref, _ = measure_total_ref(wb, "overdue", "not_due")
    over, not_due = cached(wb, [over_ref, nd_ref])
    bounds = input_of(spec_of(dest), "bucket_from")["values"]
    rows = _days_and_amounts(dest)
    last = sum(a for d, a in rows if d >= bounds[-1]) / 1000
    first = sum(a for d, a in rows if bounds[0] <= d < bounds[1]) / 1000
    assert _close(over, ship_over) and _close(over, last), (over, ship_over, last)
    assert _close(not_due, ship_nd) and _close(not_due, first), (not_due, ship_nd, first)


@needs_plugin
@pytest.mark.parametrize("value", [120, 30], ids=["120", "30"])
def test_ar_aging_cutoff_input_is_fixed_at_90(value: int, outside_tmp: Path) -> None:
    """The input over90_days is fixed at 90 (valid range 90 to 90; its label says so): build of a copy with another
    value exits 2, writes no workbook and prints the range and the input's note."""
    item = input_of(spec_of(AR), "over90_days")
    assert (item["value"], item["valid"], item["unit"]) == (90, {"min": 90, "max": 90}, "days"), item
    assert "(fixed at 90)" in item["label"] and "Over 90 days" in item["note"], item
    dest, env = copy_model(AR, outside_tmp, excel=False)
    spec = spec_of(dest)
    input_of(spec, "over90_days")["value"] = value
    common.write_json(dest / "model.spec.json", spec)
    proc = rotm("build", dest, "--force", env=env)
    text = out(proc)
    assert proc.returncode == 2, text[-3000:]
    assert "outside its valid range 90 to 90" in text, text[-3000:]
    assert flat(item["note"]) in flat(text), text[-3000:]
    assert not (dest / "model.xlsx").exists() and not (dest / "model.manifest.json").exists()


@needs_plugin
def test_ar_aging_labels_do_not_read_the_buckets() -> None:
    """The measures not_due and over90 and the checks pct_ties and over90_ties read the days past due, not the
    aging grid's columns, the bucket names or the bucket numbers; no text of the specification states a number of
    buckets; the README says what a change of the bucket lists changes and what it does not."""
    spec = spec_of(AR)
    measures = {m["id"]: m for m in block_of(spec, "overdue")["measures"]}
    bucket_words = ("{grow:aging}", "{in:bucket_names}", "{in:bucket_from}", "open_items.bucket}",
                    "open_items.bucket_no}")
    for mid in ("not_due", "over90"):
        assert not any(w in measures[mid]["expr"] for w in bucket_words), measures[mid]
    assert "{h:open_items.over90_k}" in measures["over90"]["expr"]
    assert "{h:open_items.not_due_k}" in measures["not_due"]["expr"]
    helpers = {c["id"]: c for c in sheet_of(spec, "helpers")["columns"]}
    assert "{in:over90_days}" in helpers["over90_k"]["expr"] and "{this:days_past_due}>" in helpers["over90_k"]["expr"]
    assert "{this:days_past_due}<=0" in helpers["not_due_k"]["expr"]
    for cid in ("pct_ties", "over90_ties"):
        c = check_of(spec, cid)
        sides = c["actual"] + c["expected"]
        assert not any(w in sides for w in bucket_words), c
        assert "{h:open_items.days_past_due}" in c["expected"], c
        assert "_k}" not in c["expected"].replace("{h:open_items.open_k}", ""), c     # another path than the measure
    texts = [t for _p, t in _strings(spec)]
    assert not [t for t in texts if re.search(r"\b(two|three|four|five|six|seven)\s+buckets\b", t, re.I)]
    readme = flat((AR / "README.md").read_text(encoding="utf-8"))
    assert "five buckets" not in readme
    for words in ("What a change of the bucket lists changes:", "What it does not change:", "valid range 90 to 90",
                  "outside its valid range 90 to 90"):
        assert words in readme, words


# ============================================================================= the three other inputs (documents)

def _strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    """(JSON path, text) of every string of a specification, in the path form build's messages use."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            found += _strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += _strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        found.append((path, node))
    return found


def _at(spec: Any, path: str) -> Any:
    node = spec
    for key, index in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", path):
        node = node[key] if key else node[int(index)]
    return node


def _listed(readme: Path) -> dict[str, tuple[str, str]]:
    """JSON path -> (input column, text) of every table row of the README section 'Texts that follow an input'."""
    raw = readme.read_text(encoding="utf-8")
    assert f"\n{TEXTS_SECTION}\n" in raw, readme
    section = raw.split(f"\n{TEXTS_SECTION}\n", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, tuple[str, str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) == 3 and cells[1].startswith("`") and cells[1] != "---":
            rows[cells[1].strip("`")] = (cells[0], cells[2])
    return rows


def _texts_follow_the_input(folder: Path, inputs: tuple[str, ...], pattern: str, *, flags: int = 0) -> None:
    """Every text of the specification that names the input's value (pattern) is listed in the README with its JSON
    path and its exact text; every listed path holds the listed text; the input items themselves are left out."""
    spec = spec_of(folder)
    own = [f"sheets[{si}].items[{ii}]" for si, s in enumerate(spec["sheets"]) if s["type"] == "inputs"
           for ii, it in enumerate(s["items"]) if it["id"] in inputs]
    named = {p: t for p, t in _strings(spec) if re.search(pattern, t, flags)
             and not any(p == o or p.startswith(o + ".") for o in own)
             and not p.endswith((".expr", ".actual", ".expected", ".cell", ".bound", ".first"))}
    assert named, pattern
    listed = _listed(folder / "README.md")
    missing = sorted(set(named) - set(listed))
    assert not missing, missing
    for p, (who, text) in listed.items():
        assert _at(spec, p) == text, (p, text)
        assert all(f"`{i}`" in who for i in inputs), (p, who)


@needs_plugin
def test_ar_aging_readme_names_the_texts_that_follow_the_as_of_date() -> None:
    _texts_follow_the_input(AR, ("as_of",), r"30 June 2026|June 2026|2026-06-30")
    assert input_of(spec_of(AR), "as_of")["value"] == "2026-06-30"


@needs_plugin
def test_price_volume_mix_readme_names_the_texts_that_follow_the_period_codes() -> None:
    _texts_follow_the_input(PVM, ("py_label", "cy_label"), r"\b(PY|CY)\b|(?i:prior|current)")
    readme = flat((PVM / "README.md").read_text(encoding="utf-8"))
    assert "With the two codes swapped, every check holds" in readme


@needs_plugin
def test_rolling_forecast_readme_names_the_texts_that_follow_the_month_split() -> None:
    _texts_follow_the_input(RF, ("actual_months", "forecast_months"), r"\b3\s*\+\s*9\b")
    spec = spec_of(RF)
    assert (input_of(spec, "actual_months")["value"], input_of(spec, "forecast_months")["value"]) == (3, 9)


# ============================================================================= [A-aera-4, A-aera-13] part (2)

# folder -> [(block id or None, check id, sheet of the block)]: the per-cell checks of ratio grids (v7)
PER_CELL = {
    "sales_vs_target": [("pct_cell_diff", "pct_cell_ties", "calc"), ("var_pct_cell_diff", "var_pct_cell_ties", "calc")],
    "rolling_forecast": [("runrate_cell_diff", "runrate_cell_ties", "calc")],
}
V8_LATER = {"closed_months_landed", "landed"}            # v8 [A-aera-3]: the check and the block added after them


def _folder_of(name: str) -> Path:
    return EXAMPLE if name == "sales_vs_target" else TEMPLATES / name


def _per_cell_param(name: str) -> Any:
    return pytest.param(name, id=name, marks=[] if name == "sales_vs_target" else [needs_plugin])


@pytest.mark.parametrize("name", [_per_cell_param(n) for n in PER_CELL])
def test_per_cell_ratio_checks_are_written_after_the_existing_ones(name: str) -> None:
    """Each new block is the last block of its sheet and each new check follows the existing error checks (so no
    cell that a document or test names moves); each check is at_most against {in:check_tolerance} and holds no
    IFERROR or IFNA; the summed-product checks stay."""
    spec = spec_of(_folder_of(name))
    checks = sheet_of(spec, "checks")["checks"]
    ids = [c["id"] for c in checks]
    # v8 ([A-aera-3], P2): rolling_forecast's check closed_months_landed and block landed are written after these
    errors = [c["id"] for c in checks if c.get("severity", "error") == "error" and c["id"] not in V8_LATER]
    new = [cid for _b, cid, _s in PER_CELL[name]]
    assert errors[-len(new):] == new, errors
    calc = next(s for s in spec["sheets"] if s["name"] == "calc")
    blocks = [b for b in calc["blocks"] if b["id"] not in V8_LATER]
    assert [b["id"] for b in blocks[-len(new):]] == [b for b, _c, _s in PER_CELL[name]], calc["blocks"]
    for bid, cid, _sheet in PER_CELL[name]:
        c = check_of(spec, cid)
        assert c["kind"] == "at_most" and c["bound"] == "{in:check_tolerance}", c
        assert c["actual"] == f"MAX({{blk:{bid}}})", c
        assert len(c["label"]) <= 80, c["label"]
        b = block_of(spec, bid)
        exprs = [b["cell"]] if "cell" in b else [i["expr"] for i in b["items"]]
        assert exprs and all(e.startswith("ABS(") for e in exprs), b
        assert not any(re.search(r"IFERROR|IFNA", e, re.I) for e in exprs + [c["actual"]]), (b, c)
    kept = {"sales_vs_target": ("pct_grid_ties", "var_pct_all"), "rolling_forecast": ("runrate_ties",)}[name]
    assert all(k in ids for k in kept), ids
    if name == "sales_vs_target":
        assert block_of(spec, "pct_cell_diff")["cell"] == "ABS({g:variance_pct}*{g:target}-({g:target}<>0)*{g:variance})"
        assert ids.index("far_below") > ids.index("var_pct_cell_ties")


@pytest.mark.parametrize("name", [_per_cell_param(n) for n in PER_CELL])
def test_per_cell_ratio_checks_build_within_the_limits(name: str, outside_tmp: Path) -> None:
    """build --strict of a copy exits 0 with no F-LENGTH or F-DEPTH; the compiled formulas of the new checks and
    of the new block keep within the length limit; every block and check of the shipped manifest keeps its cell,
    except the alert rows below the checks (they move down by one row per new check)."""
    folder = _folder_of(name)
    dest, env = copy_model(folder, outside_tmp, excel=False)
    proc = rotm("build", dest, "--strict", "--force", env=env)
    text = out(proc)
    assert proc.returncode == 0, text[-3000:]
    assert "F-LENGTH" not in text and "F-DEPTH" not in text, text[-3000:]
    import openpyxl
    man = manifest.load_manifest(dest / "model.xlsx")
    wb = openpyxl.load_workbook(dest / "model.xlsx")
    try:
        for bid, cid, block_sheet in PER_CELL[name]:
            entry = next(c for c in man["checks"] if c["id"] == cid)
            sheet, cell = entry["result"].split("!")
            row = wb[sheet][cell].row
            for col in ("D", "E"):                                      # Actual and bound (build.CHK)
                formula = str(wb[sheet][f"{col}{row}"].value)
                assert formula.startswith("=") and len(formula) - 1 <= LENGTH_LIMIT, formula
            blk = next(b for s in man["sheets"] for b in s.get("blocks", []) if b["id"] == bid)
            first = blk["body"].split(":")[0]
            formula = str(wb[block_sheet][first].value)
            assert formula.startswith("=ABS(") and len(formula) - 1 <= LENGTH_LIMIT, formula
    finally:
        wb.close()
    old = manifest.load_manifest(folder / "model.xlsx")
    new_checks = {c["id"]: c["result"] for c in man["checks"]}
    added = [cid for _b, cid, _s in PER_CELL[name]]
    for c in old["checks"]:
        if c["id"] in added:
            continue
        if c.get("severity") == "alert":
            continue
        assert new_checks[c["id"]] == c["result"], (c["id"], c["result"], new_checks[c["id"]])
    new_blocks = {b["id"]: b["body"] for s in man["sheets"] for b in s.get("blocks", [])}
    for s in old["sheets"]:
        for b in s.get("blocks", []):
            assert new_blocks[b["id"]] == b["body"], (b["id"], b["body"], new_blocks[b["id"]])
    assert {k: v for k, v in man["cells"].items() if k in old["cells"]} == old["cells"]


# The ratio grid replaced by an overall rate (every cell the same rate): the summed-product check still holds, the
# per-cell check fails and with it the master check.
#   example_column_rate: each region-month shows its month's rate (the planner's case);
#   example_overall_rate: each region-month shows the six months' rate (the accuracy reviewer's case, -1.2323 %);
#   example_bridge_overall_rate: the item var_pct of the block bridge shows the six months' rate in every month;
#   rolling_forecast_runrate: each product shows the run rate of all products together.
REPLACED = {
    "example_column_rate": ("sales_vs_target", "variance_pct", None,
                            "IFERROR({b:variance.total}/{b:target.total},0)", "pct_cell_ties", "pct_grid_ties"),
    "example_overall_rate": ("sales_vs_target", "variance_pct", None,
                             "IFERROR({blk:variance.total}/{blk:target.total},0)", "pct_cell_ties", "pct_grid_ties"),
    "example_bridge_overall_rate": ("sales_vs_target", "bridge", "var_pct",
                                    "IFERROR({blk:variance.total}/{blk:target.total},0)", "var_pct_cell_ties",
                                    "var_pct_all"),
    "rolling_forecast_runrate": ("rolling_forecast", "runrate", None,
                                 "SUM({blk:actual})/{in:actual_months}/ROWS({blk:actual})", "runrate_cell_ties",
                                 "runrate_ties"),
}


def _replaced_param(case: str) -> Any:
    marks = [pytest.mark.excel] + ([] if REPLACED[case][0] == "sales_vs_target" else [needs_plugin])
    return pytest.param(case, id=case, marks=marks)


@pytest.mark.parametrize("case", [_replaced_param(c) for c in REPLACED])
def test_per_cell_check_fails_when_every_cell_holds_an_overall_rate(case: str, outside_tmp: Path) -> None:
    """Excel: a copy whose ratio grid (or item) holds an overall rate in every cell: build --strict exits 0, check
    --strict exits 1, the per-cell check fails and the master check is 1, while the summed-product check holds."""
    name, bid, item, expr, per_cell, summed = REPLACED[case]
    dest, env = copy_model(_folder_of(name), outside_tmp, excel=True)
    spec = spec_of(dest)
    blk = block_of(spec, bid)
    if item:
        next(i for i in blk["items"] if i["id"] == item)["expr"] = expr
    else:
        blk["cell"] = expr
    common.write_json(dest / "model.spec.json", spec)
    _built, checked = build_and_check(dest, env)
    assert checked.returncode == 1, out(checked)[-3000:]
    states = check_states(dest)
    assert states[per_cell] == "fail" and states[summed] == "pass", states
    failed = sorted(k for k, v in states.items() if v != "pass")
    assert failed == [per_cell], failed
    man = manifest.load_manifest(dest / "model.xlsx")
    assert cached(dest / "model.xlsx", [man["master_check"]]) == [1]


@pytest.mark.parametrize("name", [pytest.param(n, id=n, marks=[pytest.mark.excel] +
                                               ([] if n == "sales_vs_target" else [needs_plugin])) for n in PER_CELL])
def test_per_cell_checks_hold_on_the_unchanged_copy(name: str, outside_tmp: Path) -> None:
    """Excel: the unchanged copy: check --strict exits 0, every per-cell check holds and its actual is at most the
    tolerance."""
    dest, env = copy_model(_folder_of(name), outside_tmp, excel=True)
    _built, checked = build_and_check(dest, env)
    assert checked.returncode == 0, out(checked)[-3000:]
    states = check_states(dest)
    for _b, cid, _s in PER_CELL[name]:
        assert states[cid] == "pass", (cid, states)
    report = json.loads((dest / "reports" / "check.json").read_text(encoding="utf-8"))
    for c in report["data"]["checks"]:
        if c["id"] in [cid for _b, cid, _s in PER_CELL[name]]:
            assert isinstance(c["actual"], (int, float)) and 0 <= c["actual"] <= 0.001, c


# ============================================================================= plan 6.2 step 5: lift ties out

CHANGED = {"sales_vs_target": EXAMPLE, "ar_aging": AR, "rolling_forecast": RF}


@pytest.mark.excel
@pytest.mark.parametrize("name", [pytest.param(n, id=n, marks=[] if n == "sales_vs_target" else [needs_plugin])
                                  for n in CHANGED])
def test_changed_folders_tie_out_with_lift(name: str, outside_tmp: Path) -> None:
    """Excel: a copy of each folder whose specification v7 changed: build --strict, check --strict (Excel), then
    rotm.py lift <copy> --run exits 0 and every compared value ties out (v7: the one-expression form
    SUMPRODUCT(ABS(...)) gave #VALUE! in the script, so the bridge got a block instead)."""
    dest, env = copy_model(CHANGED[name], outside_tmp, excel=True)
    _built, checked = build_and_check(dest, env)
    assert checked.returncode == 0, out(checked)[-3000:]
    lifted = rotm("lift", dest, "--run", env=env, timeout=1800)
    text = out(lifted)
    assert lifted.returncode == 0, text[-3000:]
    assert "Result: every compared value ties out" in text, text[-3000:]

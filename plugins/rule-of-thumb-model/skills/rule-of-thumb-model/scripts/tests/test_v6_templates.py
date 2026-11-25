"""Tests of the v6 template changes (package P7 of the v6 plan). Owner: P7.

Entry [A-aera-3, C-sol-2] (major): in the worked example (examples/sales_vs_target), ar_aging and price_volume_mix
every unit label of an amount is fixed text "KRW thousand", and the display scale divides the landed KRW amounts.
v5 allowed a scale from 1 to 1,000,000, so a scale of 1 showed amounts in KRW under labels that say KRW thousand
with every check holding. v6 fixes the scale at 1000 in the three specifications, as budget_vs_actual and
rolling_forecast have it since v5 (valid range 1000 to 1000, the label 'Display scale (fixed at 1000)', a note that
every unit label of an amount is in KRW thousand); task.md and the two READMEs say what a model in another unit
changes. The tests:
- each other value (1 and 1,000,000) is refused by build with the note (exit 2, no workbook);
- at 1000 (the only value left) the amount on the summary sheet and its unit label agree with the raw total divided
  by 1000 after recalculation in Excel (one test, three cases);
- every shipped specification: an input that scales displayed amounts is fixed; the starter has none;
- task.md and the two READMEs state the fixed scale.

Tests that read PLUGIN/templates skip with a reason when the skill folder is copied alone (v5 plan, I-8). Build
copies lie in outside_tmp (conftest.py), so the tests pass wherever --basetemp points.
"""
from __future__ import annotations

import csv
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
EXAMPLE = SKILL / "examples" / "sales_vs_target"
STARTER = SKILL / "templates" / "starter"

SCALE_LABEL = "Display scale (fixed at 1000)"
FIXED_RANGE = {"min": 1000, "max": 1000}
UNIT = "KRW thousand"

# folder name -> (path, the document that explains the scale, the helper column that divides by the scale)
SCALED = {
    "sales_vs_target": (EXAMPLE, "task.md", "amount_k"),
    "ar_aging": (TEMPLATES / "ar_aging", "README.md", "open_k"),
    "price_volume_mix": (TEMPLATES / "price_volume_mix", "README.md", "revenue_k"),
}

# The key amount on the summary (output) sheet, chosen from the manifest, and the raw total it shows divided by the
# scale: folder -> (block id, measure or item id, raw table, raw column, (filter column, filter value) or None).
# - sales_vs_target: the total of the measure act (Actual) of block ytd, summary!D11 in v5: SUM(raw sales.amount).
# - ar_aging: the total of the measure total (Open) of block overdue, summary!D15 in v5: SUM(raw open_items.open_amount).
# - price_volume_mix: the item b_end (Revenue, current period) of block bridge, summary!D10 in v5: the revenue of
#   the raw sales lines of period CY.
KEY_AMOUNT = {
    "sales_vs_target": ("ytd", "act", "sales", "amount", None),
    "ar_aging": ("overdue", "total", "open_items", "open_amount", None),
    "price_volume_mix": ("bridge", "b_end", "sales", "revenue", ("period", "CY")),
}


def _folder(name: str) -> Any:
    marks = [] if name == "sales_vs_target" else [needs_plugin]
    return pytest.param(name, id=name, marks=marks)


FOLDERS = [_folder(n) for n in SCALED]


def rotm(*args: object, env: dict[str, str] | None = None, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


def out(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def flat(text: str) -> str:
    return " ".join(text.split())


def spec_of(folder: Path) -> dict[str, Any]:
    return common.read_json(folder / "model.spec.json")


def input_items(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for s in spec["sheets"] if s.get("type") == "inputs" for i in s.get("items", [])]


def input_of(spec: dict[str, Any], input_id: str) -> dict[str, Any]:
    return next(i for i in input_items(spec) if i["id"] == input_id)


def copy_with_example_roots(folder: Path, root: Path) -> tuple[Path, dict[str, str]]:
    """A copy of a shipped folder (without its workbook, manifest and reports) under root that keeps the shipped
    agreement: channel example is accepted under root (ROTM_EXAMPLE_ROOTS, as test_v5_templates.py does)."""
    dest = root / folder.name
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "reports", "lift", "*.xlsx",
                                                                "*.manifest.json"))
    env = dict(os.environ, ROTM_EXAMPLE_ROOTS=str(root), ROTM_NO_EXCEL="1", PYTHONUTF8="1")
    return dest, env


def set_scale(root: Path, value: float) -> None:
    spec = spec_of(root)
    input_of(spec, "scale")["value"] = value
    common.write_json(root / "model.spec.json", spec)


def shipped_specs() -> list[Path]:
    folders = [EXAMPLE, STARTER]
    if HAS_PLUGIN:
        folders += sorted(p for p in TEMPLATES.iterdir() if (p / "model.spec.json").is_file())
    return folders


def units_in(node: Any) -> list[str]:
    """Every "unit" value in a specification (inputs, helper columns, blocks, measures, items)."""
    found: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("unit"), str):
            found.append(node["unit"])
        for v in node.values():
            found += units_in(v)
    elif isinstance(node, list):
        for v in node:
            found += units_in(v)
    return found


# ============================================================================= other values are refused

@pytest.mark.parametrize("value", [1, 1000000], ids=["1", "1000000"])
@pytest.mark.parametrize("name", FOLDERS)
def test_display_scale_other_values_are_refused(name: str, value: int, outside_tmp: Path) -> None:
    """Each value of the display scale other than 1000 is refused by build: exit 2, no workbook, the range and the
    input's note (which names KRW thousand) in the output. v5 built the three at 1 and at 1,000,000 with exit 0."""
    folder = SCALED[name][0]
    dest, env = copy_with_example_roots(folder, outside_tmp)
    set_scale(dest, value)
    proc = rotm("build", dest, "--force", env=env)
    text = out(proc)
    assert proc.returncode == 2, text[-3000:]
    assert "outside its valid range 1000 to 1000" in text, text[-3000:]
    note = input_of(spec_of(dest), "scale").get("note", "")
    assert UNIT in note and flat(note) in flat(text), (note, text[-3000:])
    assert not (dest / "model.xlsx").exists()
    assert not (dest / "model.manifest.json").exists()


# ============================================================================= every shipped specification

def test_every_shipped_display_scale_is_fixed() -> None:
    """Every shipped specification: an input that scales displayed amounts (id scale, or unit 'KRW per unit shown')
    has the valid range 1000 to 1000, the label 'Display scale (fixed at 1000)' and a note that every unit label is
    in KRW thousand, ending with the document that says what a model in another unit changes; its value, type,
    format and unit are as in v5. A specification with a unit KRW thousand has such an input; the starter has none
    and no unit KRW thousand. Every helper column whose unit is KRW thousand divides by a fixed input."""
    seen = []
    for folder in shipped_specs():
        spec = spec_of(folder)
        scales = [i for i in input_items(spec) if i["id"] == "scale" or i.get("unit") == "KRW per unit shown"]
        has_unit = UNIT in units_in(spec)
        if folder == STARTER:
            assert not scales and not has_unit, (folder.name, scales)
            continue
        assert len(scales) == 1 and has_unit, (folder.name, scales)
        s = scales[0]
        doc = "task.md" if folder == EXAMPLE else "README"
        assert s.get("valid") == FIXED_RANGE, (folder.name, s)
        assert s["label"] == SCALE_LABEL, (folder.name, s["label"])
        note = s.get("note", "")
        assert "every unit label" in note and UNIT in note, (folder.name, note)
        assert note.rstrip(".").endswith(f"({doc})"), (folder.name, note)
        assert (s["value"], s["type"], s["format"], s["unit"]) == (1000, "number", "count", "KRW per unit shown"), s
        fixed = {i["id"] for i in input_items(spec)
                 if isinstance(i.get("valid"), dict) and "min" in i["valid"] and i["valid"].get("min") ==
                 i["valid"].get("max")}
        for sheet in spec["sheets"]:
            for col in sheet.get("columns", []):
                if col.get("unit") == UNIT:
                    divisors = re.findall(r"/\{in:([A-Za-z0-9_]+)\}", col["expr"])
                    assert divisors and set(divisors) <= fixed, (folder.name, col["id"], col["expr"])
        seen.append(folder.name)
    expected = {"sales_vs_target"} | ({"ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast"}
                                      if HAS_PLUGIN else set())
    assert set(seen) == expected, seen


# ============================================================================= task.md and the READMEs

@pytest.mark.parametrize("name", FOLDERS)
def test_fixed_scale_is_stated_in_task_md_and_the_readmes(name: str) -> None:
    """task.md of the worked example and the READMEs of ar_aging and price_volume_mix have the section 'Display
    scale and units' (the words of test_v5_templates.py::test_fixed_scale_is_stated_in_the_readme, without 'budget
    retyped': these three have no typed amounts), the refusal and its exit code, and the count of the places that
    hold "unit": "KRW thousand", which equals the count in the specification."""
    folder, doc, helper = SCALED[name]
    raw = (folder / doc).read_text(encoding="utf-8")
    assert "\n## Display scale and units\n" in raw, doc
    text = flat(raw)
    needed = ["fixed at 1000", "valid range 1000 to 1000", UNIT, "another unit", "labels changed",
              '"unit": "KRW thousand"', f"({UNIT})", f"`{helper}`", "outside its valid range", "exit 2",
              "input 'scale' (Display scale (fixed at 1000)) holds <value>, outside its valid range 1000 to 1000"]
    missing = [n for n in needed if n not in text]
    assert not missing, missing
    stated = re.search(r'every `"unit": "KRW thousand"` \((\d+) places', text)
    assert stated, "the count of the places that hold the unit KRW thousand"
    assert int(stated.group(1)) == units_in(spec_of(folder)).count(UNIT), (stated.group(1), name)


# ============================================================================= Excel: the amount and its label

def _key_amount(workbook: Path, name: str) -> tuple[float, list[str], str]:
    """The key amount of the summary sheet (KEY_AMOUNT), read after recalculation: its value, its unit labels (the
    cell of the Unit column in its row; for a measure also the unit row under the measure's header) and its cell."""
    import openpyxl
    block_id, part, _t, _c, _f = KEY_AMOUNT[name]
    man = manifest.load_manifest(workbook)
    sheet = next(s for s in man["sheets"] if s.get("type") == "output")
    block = next(b for b in sheet["blocks"] if b["id"] == block_id)
    wb = openpyxl.load_workbook(workbook, data_only=True)
    try:
        ws = wb[sheet["name"]]
        header = block["header_row"]
        unit_col = next(c.column_letter for c in ws[header] if c.value == "Unit")
        if block["shape"] == "measures":
            col = next(m["col"] for m in block["measures"] if m["id"] == part)
            row = block["total_row"]
            labels = [ws[f"{unit_col}{row}"].value, ws[f"{col}{header + 1}"].value]
        else:
            row = next(i["row"] for i in block["items"] if i["id"] == part)
            col = re.match(r"[A-Z]+", block["body"]).group(0)
            labels = [ws[f"{unit_col}{row}"].value]
        value = ws[f"{col}{row}"].value
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (name, f"{col}{row}", value)
        return float(value), labels, f"{sheet['name']}!{col}{row}"
    finally:
        wb.close()


def _raw_total(root: Path, name: str) -> float:
    _b, _p, table, column, where = KEY_AMOUNT[name]
    with (root / "raw" / f"{table}.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, table
    return sum(float(r[column]) for r in rows if where is None or r[where[0]] == where[1])


@pytest.mark.excel
@pytest.mark.parametrize("name", FOLDERS)
def test_summary_amount_and_its_unit_label_agree_with_the_raw_total(name: str, outside_tmp: Path) -> None:
    """For every value the specification allows for the display scale (the ends of its valid range and its value;
    since v6 only 1000): build --strict of a copy at that value exits 0 and, after recalculation in Excel, the key
    amount of the summary sheet (KEY_AMOUNT) is labelled KRW thousand and equals the raw total divided by 1000
    (relative difference at most 1e-6). v5 allowed 1, where the amount was the raw total in KRW under the label KRW
    thousand. The values are tried from the smallest, so v5 fails at the first."""
    from lib import excel
    folder = SCALED[name][0]
    scale = input_of(spec_of(folder), "scale")
    allowed = sorted({scale["valid"]["min"], scale["valid"]["max"], scale["value"]})
    for value in allowed:
        dest, env = copy_with_example_roots(folder, outside_tmp / f"s{value}")
        set_scale(dest, value)
        proc = rotm("build", dest, "--strict", "--force", env=env)
        assert proc.returncode == 0, (value, out(proc)[-3000:])
        wb = dest / "model.xlsx"
        res = excel.recalc(wb)
        assert res.ok and not res.error_cells, (value, res.errors, res.error_cells[:5])
        amount, labels, cell = _key_amount(wb, name)
        expected = _raw_total(dest, name) / 1000
        assert labels and all(lb == UNIT for lb in labels), (value, cell, labels)
        assert expected != 0 and abs(amount - expected) <= 1e-6 * abs(expected), (value, cell, amount, expected)

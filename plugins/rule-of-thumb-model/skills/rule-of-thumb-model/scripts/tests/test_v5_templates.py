"""Tests of the v5 template changes (package P7 of the v5 plan). Owner: P7.

Entries:
- [A-aera-1] (major): the display scale of budget_vs_actual and rolling_forecast is fixed at 1000 (valid range 1000
  to 1000; the label and the note say why); build refuses another value (P2's valid-range refusal: integration test
  I-2); each README says what to change for a model in another unit. The other shipped specifications with a scale
  (the worked example, ar_aging, price_volume_mix) divide both sides of every comparison by it. v6 (r5 verdict
  [A-aera-3, C-sol-2]): their scale is fixed at 1000 too; the v5 test that built them at both ends of the range 1 to
  1,000,000 is removed, and test_v6_templates.py tests the fixed scale of all five.
- Supporting [A-aera-9, C-sol-14, J-2] part (1): the percentage checks of budget_vs_actual, rolling_forecast and
  ar_aging are written in the product form (no division by the denominator on the expected side); a budget of 0
  holds every check.
- Supporting [A-aera-15]: the calculation sheet of price_volume_mix sets "font_size": 9.

Tests that read PLUGIN/templates skip with a reason when the skill folder is copied alone (v5 plan, I-8). Tests
marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib import common, contract, land, manifest, modelfolder

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
NO_PLUGIN = "the skill folder was copied without its plugin, so PLUGIN/templates is absent"
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason=NO_PLUGIN)
TEMPLATES = PLUGIN / "templates"
EXAMPLE = SKILL / "examples" / "sales_vs_target"

FIXED = ("budget_vs_actual", "rolling_forecast")                  # templates whose scale is fixed at 1000
FOUR = ("ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast")
SCALE_LABEL = "Display scale (fixed at 1000)"
SCALE_NOTE = ("Fixed: the budget and every unit label of this template are in KRW thousand. A model in another unit "
              "needs the budget retyped and the labels changed (README).")
REPLY = "agreed, land it as proposed."
LENGTH_LIMIT = 100                                                 # characters (rules of the style dictionary)

# (template, check id, the denominator as the specification writes it, the label of the check)
PCT_CHECKS = (
    ("budget_vs_actual", "ytd_pct_ties", "{blk:budget_grid.total}",
     "YTD variance % times budget total equals variance total (budget not 0)"),
    ("rolling_forecast", "fy_pct_ties", "SUM({in:budget})",
     "Full-year variance % times budget equals outlook less budget (budget not 0)"),
    ("ar_aging", "pct_ties", "{blk:aging.total}",
     "Past due % times total equals total less not-due amount (total not 0)"),
)


def rotm(*args: object, env: dict[str, str] | None = None, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


def out(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def spec_of(folder: Path) -> dict[str, Any]:
    return common.read_json(folder / "model.spec.json")


def sheet_of(spec: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in spec["sheets"] if s["name"] == name)


def input_of(spec: dict[str, Any], input_id: str) -> dict[str, Any]:
    return next(i for i in sheet_of(spec, "inputs")["items"] if i["id"] == input_id)


def check_of(spec: dict[str, Any], check_id: str) -> dict[str, Any]:
    return next(c for c in sheet_of(spec, "checks")["checks"] if c["id"] == check_id)


def copy_with_example_roots(folder: Path, tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A copy of a shipped folder (without its workbook, manifest and reports) that keeps the shipped agreement:
    channel example is accepted under tmp_path (ROTM_EXAMPLE_ROOTS, as test_examples.py does). No Excel."""
    dest = tmp_path / folder.name
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "reports", "lift", "*.xlsx",
                                                                "*.manifest.json"))
    env = dict(os.environ, ROTM_EXAMPLE_ROOTS=str(tmp_path), ROTM_NO_EXCEL="1", PYTHONUTF8="1")
    return dest, env


def model_from(folder: Path, root: Path) -> Path:
    """A new model folder made from a shipped folder as a user makes it: init --template (contracts back to
    proposed, no raw/, queries/ or workbook), each contract agreed again on the task-file channel, each table
    landed from the sample extract in data/."""
    marker = modelfolder.init_model(root, template=str(folder))
    task = root.parent / f"{root.name}_task.md"
    task.write_text(f"Contract reply: {REPLY}\n", encoding="utf-8")
    for t in marker["reset_tables"]:
        contract.agree(root, t, reply=REPLY, by="tester", channel="task-file", source_file=task)
        rep = land.land_csv(root, t, root / "data" / f"{t}.csv")
        assert rep.exit_code == 0, [f.line() for f in rep.findings]
    return root


def set_scale(root: Path, value: float) -> None:
    spec = spec_of(root)
    input_of(spec, "scale")["value"] = value
    common.write_json(root / "model.spec.json", spec)


# ============================================================================= [A-aera-1] the fixed scale

@needs_plugin
@pytest.mark.parametrize("name", FIXED)
def test_fixed_scale_builds_at_1000(name: str, tmp_path: Path) -> None:
    """The scale input of both templates: valid range 1000 to 1000, its label and note say why (v5 plan 5.2); the
    value, unit, format and source are as before; build --strict of a copy exits 0."""
    scale = input_of(spec_of(TEMPLATES / name), "scale")
    assert scale["valid"] == {"min": 1000, "max": 1000}, scale
    assert scale["label"] == SCALE_LABEL and scale["note"] == SCALE_NOTE, scale
    assert (scale["value"], scale["type"], scale["format"], scale["unit"], scale["source"]) == \
        (1000, "number", "count", "KRW per unit shown", "template"), scale
    dest, env = copy_with_example_roots(TEMPLATES / name, tmp_path)
    proc = rotm("build", dest, "--strict", "--force", env=env)
    assert proc.returncode == 0, out(proc)[-3000:]


@needs_plugin
@pytest.mark.parametrize("value", [1, 1000000], ids=["1", "1000000"])
@pytest.mark.parametrize("name", FIXED)
def test_fixed_scale_other_values_are_refused(name: str, value: int, tmp_path: Path) -> None:
    """Integration test I-2: needs P2's valid-range refusal (v5 plan 5.1). Each other value of the fixed scale is
    refused by build (exit 2, nothing built) with the range and the note's reason."""
    dest, env = copy_with_example_roots(TEMPLATES / name, tmp_path)
    set_scale(dest, value)
    proc = rotm("build", dest, "--force", env=env)
    text = out(proc)
    assert proc.returncode == 2, text[-3000:]
    assert "outside its valid range 1000 to 1000" in text, text[-3000:]
    assert "in KRW thousand" in text, text[-3000:]
    assert not (dest / "model.xlsx").exists()


@needs_plugin
@pytest.mark.parametrize("name", FIXED)
def test_fixed_scale_is_stated_in_the_readme(name: str) -> None:
    text = " ".join((TEMPLATES / name / "README.md").read_text(encoding="utf-8").split())
    helper = {"budget_vs_actual": "amount_k", "rolling_forecast": "revenue_k"}[name]
    needed = ["fixed at 1000", "KRW thousand", "another unit", "budget retyped", "labels changed",
              '"unit": "KRW thousand"', "(KRW thousand)", f"`{helper}`", "valid range", "outside its valid range"]
    missing = [n for n in needed if n not in text]
    assert not missing, missing


# ============================================================================= [A-aera-1] the other shipped scales
# v6 (r5 verdict [A-aera-3, C-sol-2]): the scales of the worked example, ar_aging and price_volume_mix are fixed at
# 1000 as well (their labels say KRW thousand as fixed text). test_scale_ends_keep_every_percentage_and_sign (and its
# helpers _template, _scale_folders, _numbers and _sign) is removed: it built these three at 1 and at 1,000,000;
# build refuses both values now (test_v6_templates.py::test_display_scale_other_values_are_refused).

def test_no_other_shipped_input_converts_one_side_only() -> None:
    """The shipped specifications other than the two fixed templates: an input that converts a unit (the display
    scale) divides both sides of every check it appears in, and every helper amount that the blocks compare."""
    folders = [EXAMPLE, SKILL / "templates" / "starter"] + ([TEMPLATES / n for n in ("ar_aging", "price_volume_mix")]
                                                           if HAS_PLUGIN else [])
    for folder in folders:
        spec = spec_of(folder)
        ids = {i["id"] for i in sheet_of(spec, "inputs")["items"]}
        if "scale" not in ids:
            assert folder.name == "starter", folder.name                       # the starter has no scale input
            continue
        assert input_of(spec, "scale")["valid"] == {"min": 1000, "max": 1000}, folder.name   # v6: fixed
        for c in sheet_of(spec, "checks")["checks"]:
            sides = [c.get("actual") or "", c.get("expected") or ""]
            if any("{raw:" in s and "/{in:scale}" in s for s in sides):
                other = sides[0] if "{raw:" in sides[1] else sides[1]
                # the other side reads blocks or cells that are built from the helper amounts divided by scale
                assert "{raw:" not in other and "{in:scale}" not in other, (folder.name, c["id"])
            # a raw amount is never compared without the scale
            for s in sides:
                if "{raw:" in s and "SUM" in s and "qty" not in s and "COUNTA" not in s:
                    assert "/{in:scale}" in s, (folder.name, c["id"], s)
        for s in spec["sheets"]:
            for col in s.get("columns", []):
                if col.get("unit") == "KRW thousand":
                    assert col["expr"].endswith("/{in:scale}"), (folder.name, col["id"])


# ============================================================================= [A-aera-9, C-sol-14, J-2] part (1)

@needs_plugin
@pytest.mark.parametrize("name,check_id,denominator,label", PCT_CHECKS, ids=[c[0] for c in PCT_CHECKS])
def test_template_percentage_checks_do_not_divide_by_the_denominator(name: str, check_id: str, denominator: str,
                                                                     label: str) -> None:
    c = check_of(spec_of(TEMPLATES / name), check_id)
    assert "/" not in c["expected"], c["expected"]                       # no division on the expected side
    assert "/" + denominator not in c["actual"] + c["expected"], c
    assert c["actual"].endswith("*" + denominator), c["actual"]          # the percentage times the denominator
    assert c["label"] == label and len(c["label"]) <= 80, c["label"]
    assert "gives 0 %" in c.get("note", ""), c                           # what a zero denominator gives


@needs_plugin
@pytest.mark.parametrize("name", FOUR)
def test_templates_build_strict_after_the_change(name: str, tmp_path: Path) -> None:
    """build --strict of copies of the four templates exits 0 with no F-LENGTH or F-DEPTH; the compiled formulas of
    the three percentage checks keep within the length limit."""
    dest, env = copy_with_example_roots(TEMPLATES / name, tmp_path)
    proc = rotm("build", dest, "--strict", "--force", env=env)
    text = out(proc)
    assert proc.returncode == 0, text[-3000:]
    assert "F-LENGTH" not in text and "F-DEPTH" not in text, text[-3000:]
    import openpyxl
    man = manifest.load_manifest(dest / "model.xlsx")
    wb = openpyxl.load_workbook(dest / "model.xlsx")
    try:
        for tpl, check_id, _den, _label in PCT_CHECKS:
            if tpl != name:
                continue
            entry = next(c for c in man["checks"] if c["id"] == check_id)
            sheet, cell = entry["result"].split("!")
            row = wb[sheet][cell].row
            for col in ("D", "E"):                                        # Actual and Expected (build.CHK)
                formula = str(wb[sheet][f"{col}{row}"].value)
                assert formula.startswith("=") and len(formula) - 1 <= LENGTH_LIMIT, formula
    finally:
        wb.close()


@needs_plugin
@pytest.mark.excel
def test_budget_of_zero_holds_every_check(tmp_path: Path) -> None:
    """budget_vs_actual with every budget value 0: build --strict, check --strict (Excel) and audit --strict exit 0
    (before v5 the expected side of ytd_pct_ties divided by the budget total and gave #DIV/0!)."""
    root = model_from(TEMPLATES / "budget_vs_actual", tmp_path / "zero")
    spec = spec_of(root)
    budget = input_of(spec, "budget")
    budget["values"] = [[0 for _v in row] for row in budget["values"]]
    common.write_json(root / "model.spec.json", spec)
    for args in (["build", root, "--strict", "--force"], ["check", root, "--strict"], ["audit", root, "--strict"]):
        proc = rotm(*args)
        assert proc.returncode == 0, (args[0], out(proc)[-3000:])


# ============================================================================= [A-aera-15] one sheet size

@needs_plugin
def test_price_volume_mix_calc_sets_its_size(tmp_path: Path) -> None:
    """The calc sheet of price_volume_mix (98 populated cells in v4, fewer than the 100 of sparse_sheet_cells) sets
    "font_size": 9; the built sheet uses Arial 9 and build gives it no I-SPARSE-SHEET note."""
    assert sheet_of(spec_of(TEMPLATES / "price_volume_mix"), "calc").get("font_size") == 9
    dest, env = copy_with_example_roots(TEMPLATES / "price_volume_mix", tmp_path)
    proc = rotm("build", dest, "--strict", "--force", env=env)
    assert proc.returncode == 0, out(proc)[-3000:]
    assert not any("I-SPARSE-SHEET" in ln and "'calc'" in ln for ln in out(proc).splitlines()), out(proc)[-3000:]
    import openpyxl
    wb = openpyxl.load_workbook(dest / "model.xlsx")
    try:
        sizes = {c.font.sz for row in wb["calc"].iter_rows() for c in row if c.value is not None}
    finally:
        wb.close()
    assert sizes == {9}, sizes

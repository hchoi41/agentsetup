"""The shipped worked example and templates (spec 2.4 S6, 2.7 T2 and T3; plan F14, F15). Owner: INTEGRATOR.

- Each folder is a complete model folder: marker, agreed contracts marked as the folder's own (channel example),
  raw snapshots, specification, workbook, manifest, renders (one PNG per sheet).
- The shipped workbook was built from the current specification, holds the values Excel saved, and every check and
  the master check hold 0 there; its strict audit finds nothing.
- A copy rebuilds in strict mode and audits in strict mode with exit 0 (no Excel needed).
- The worked example's numbers equal expected.json, computed from the CSV files without the workbook.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
sys.path.insert(0, str(SCRIPTS))

from lib import common, manifest, modelfolder  # noqa: E402


def _folders() -> list[Path]:
    roots = [SKILL / "examples", SKILL / "templates"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        roots.append(PLUGIN / "templates")
    out = []
    for root in roots:
        if root.is_dir():
            out += sorted(p for p in root.iterdir() if (p / "model.spec.json").is_file())
    return out


FOLDERS = _folders()
IDS = [f"{p.parent.name}/{p.name}" for p in FOLDERS]


def test_the_shipped_set_is_complete() -> None:
    names = {f"{p.parent.name}/{p.name}" for p in FOLDERS}
    assert "examples/sales_vs_target" in names
    assert "templates/starter" in names                       # the skill's own starter (F02, spec 2.1)
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        plugin_templates = [p for p in FOLDERS if p.parent == PLUGIN / "templates"]
        assert 3 <= len(plugin_templates) <= 4, [p.name for p in plugin_templates]   # plan F14: 3 to 4


@pytest.mark.parametrize("folder", FOLDERS, ids=IDS)
def test_folder_is_a_complete_model_folder(folder: Path) -> None:
    assert modelfolder.is_model_root(folder)
    for name in ("model.spec.json", "model.xlsx", "model.manifest.json", "make_data.py"):
        assert (folder / name).is_file(), name
    assert (folder / "README.md").is_file() or (folder / "task.md").is_file()
    tables = modelfolder.tables(folder)
    assert tables, "no contracts"
    for t in tables:
        state, contract, problems = modelfolder.read_contract(folder, t)
        assert state == "agreed", (t, state, problems)
        assert contract["agreement"]["channel"] == "example", t
        assert "no user gave it" in contract["agreement"]["reply"], t
        assert (folder / "raw" / f"{t}.csv").is_file(), t
        assert (folder / "queries" / f"{t}.sql").is_file(), t


@pytest.mark.parametrize("folder", FOLDERS, ids=IDS)
def test_workbook_was_built_from_the_current_spec(folder: Path) -> None:
    man = manifest.load_manifest(folder / "model.xlsx")
    assert man["spec_sha256"] == common.file_sha256(folder / "model.spec.json"), \
        "model.xlsx is older than model.spec.json: rebuild, check and render the folder"
    for entry in man["sheets"]:
        if entry.get("type") == "raw":
            snap = folder / entry["snapshot"]
            digest, rows, _cols = manifest.csv_digest(snap, entry["types"])
            assert digest == entry["digest"], f"{entry['snapshot']} differs from the landed rows the manifest records"
            assert rows == entry["rows"]


@pytest.mark.parametrize("folder", FOLDERS, ids=IDS)
def test_workbook_holds_excel_values_and_every_check_holds(folder: Path) -> None:
    import openpyxl
    man = manifest.load_manifest(folder / "model.xlsx")
    wb = openpyxl.load_workbook(folder / "model.xlsx", data_only=True, read_only=True)
    try:
        sheet, cell = man["master_check"].split("!")
        assert wb[sheet][cell].value == 0, f"master check {man['master_check']} = {wb[sheet][cell].value!r}"
        for chk in man["checks"]:
            s, c = chk["result"].split("!")
            v = wb[s][c].value
            assert v == 0, f"check {chk['id']} ({chk['result']}) = {v!r}"
    finally:
        wb.close()


@pytest.mark.parametrize("folder", FOLDERS, ids=IDS)
def test_shipped_workbook_passes_the_strict_audit(folder: Path) -> None:
    from lib import audit
    rep = audit.audit_workbook(folder / "model.xlsx", strict=True, record=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]


@pytest.mark.parametrize("folder", FOLDERS, ids=IDS)
def test_one_render_per_sheet(folder: Path) -> None:
    import openpyxl
    from lib import excel
    wb = openpyxl.load_workbook(folder / "model.xlsx", read_only=True)
    sheets = list(wb.sheetnames)
    wb.close()
    for s in sheets:
        png = folder / "reports" / "render" / f"{s}.png"
        assert png.is_file(), f"{png.name} missing: run rotm.py render"
        assert excel.png_is_blank(png) is False, f"{png.name} is blank"


@pytest.mark.parametrize("folder", FOLDERS, ids=IDS)
def test_copy_rebuilds_and_audits_in_strict_mode(folder: Path, tmp_path: Path) -> None:
    dest = tmp_path / folder.name
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "reports"))
    env = dict(os.environ, ROTM_EXAMPLE_ROOTS=str(tmp_path), PYTHONUTF8="1")
    for args in (["build", str(dest), "--strict", "--force", "--quiet"], ["audit", str(dest), "--strict", "--quiet"]):
        proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
        assert proc.returncode == 0, (args[0], proc.stdout[-2000:], proc.stderr[-2000:])


def test_copy_outside_the_plugin_without_example_roots_is_refused(outside_tmp: Path) -> None:
    """An example agreement does not travel: outside the plugin's folders build refuses it (design 5). v5:
    outside_tmp is outside the plugin wherever --basetemp points (conftest.py)."""
    src = SKILL / "templates" / "starter"
    dest = outside_tmp / "starter"
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "reports"))
    env = {k: v for k, v in os.environ.items() if k != "ROTM_EXAMPLE_ROOTS"}
    proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "build", str(dest),
                           "--strict", "--force"], capture_output=True, text=True, encoding="utf-8", env=env,
                          timeout=300)
    assert proc.returncode == 2
    assert "example" in (proc.stdout + proc.stderr)


# ----------------------------------------------------------------------------- the worked example's numbers

EXAMPLE = SKILL / "examples" / "sales_vs_target"


def _values(ref_range: str, ws) -> list[list[float]]:
    return [[c.value for c in row] for row in ws[ref_range]]


def test_worked_example_numbers_equal_the_independent_calculation() -> None:
    import openpyxl
    expected = json.loads((EXAMPLE / "expected.json").read_text(encoding="utf-8"))
    man = manifest.load_manifest(EXAMPLE / "model.xlsx")
    blocks = {b["id"]: b for s in man["sheets"] for b in s.get("blocks", [])}
    wb = openpyxl.load_workbook(EXAMPLE / "model.xlsx", data_only=True, read_only=True)
    try:
        calc = wb["calc"]
        actual = _values(blocks["actual"]["body"], calc)
        target = _values(blocks["target"]["body"], calc)
        for i, region in enumerate(("East", "North", "South", "West")):
            assert actual[i] == pytest.approx(expected["actual_by_region_month"][region], abs=1e-6)
            assert target[i] == pytest.approx(expected["target_by_region_month"][region], abs=1e-6)
        s, c = man["cells"]["pct"].split("!")
        assert wb[s][c].value == pytest.approx(expected["total"]["variance_pct"], abs=1e-12)
        s, c = man["cells"]["cum"].split("!")
        assert wb[s][c].value == pytest.approx(expected["cumulative_variance_by_month"][-1], abs=1e-6)
        ytd = _values(blocks["ytd"]["body"], wb["summary"])
        for i, region in enumerate(("East", "North", "South", "West")):
            e = expected["ytd_by_region"][region]
            assert ytd[i] == pytest.approx([e["actual"], e["target"], e["variance"], e["variance_pct"]], abs=1e-6)
    finally:
        wb.close()


def test_expected_json_is_reproducible(tmp_path: Path) -> None:
    dest = tmp_path / "ex"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("__pycache__", "reports", "*.xlsx"))
    proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(dest / "expected.py")], capture_output=True,
                          text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((dest / "expected.json").read_text(encoding="utf-8")) == \
        json.loads((EXAMPLE / "expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("folder", FOLDERS, ids=IDS)
def test_make_data_reproduces_the_landed_snapshot(folder: Path, tmp_path: Path) -> None:
    """make_data.py writes the same extract that was landed (deterministic seed)."""
    dest = tmp_path / folder.name
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "reports", "*.xlsx", "data"))
    proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(dest / "make_data.py")], capture_output=True,
                          text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    for t in modelfolder.tables(folder):
        assert (dest / "data" / f"{t}.csv").read_bytes() == (folder / "data" / f"{t}.csv").read_bytes(), t

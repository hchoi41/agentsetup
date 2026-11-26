"""Tests of the v7 integrator's changes (the requests of the eight v7 packages, 2026-11-26). Owner: the integrator.

- [A-aera-6], done in full: the minimum and the maximum of the sample profile are written like every other number
  of the contract .md (the shortest text that gives the stored number, with thousands separators). P1 had kept the
  v6 text without separators for these two columns; the shipped contract .md files were rewritten with
  `contract show`.
- Requests v7_P8 R2 and R3 ([C-sol-8]): references/google-sheets.md step 3 and the module docstring of land.py name
  both header lines land --csv accepts.
- Request v7_P8 R1 ([A-aera-3, C-sol-2]): the fix of W-PQ-NOT-ATTACHED with kept copies says that the user copies
  them back (tested in test_v7_build.py), and the references say why.
- references/checks.md: the example JSON of the per-cell check of a ratio grid is the worked example's block and
  check, as the text says.
- Request v7_P2 R1: the counts of templates/README.md equal the checks of each shipped folder.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v7_integrator.py -q -p no:cacheprovider
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, land, manifest, modelfolder

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", ""))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, ROTM_NO_EXCEL="1")
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)


def _shipped_folders() -> list[Path]:
    found = [SKILL / "examples" / "sales_vs_target", SKILL / "templates" / "starter"]
    if HAS_PLUGIN:
        found += sorted(p.parent for p in (PLUGIN / "templates").glob("*/rotm_model.json"))
    return found


# ----------------------------------------------------------------------------- [A-aera-6]: Min and Max

@pytest.mark.parametrize("value,text", [(10750, "10,750"), (10750.0, "10,750"), (897700.25, "897,700.25"),
                                        (1234567.5, "1,234,567.5"), (100, "100"), (-2500.5, "-2,500.5"),
                                        (0.30000000000000004, "0.30000000000000004"), (4.2e-07, "4.2e-07"),
                                        ("2026-01-01", "2026-01-01"), ("North", "North")])
def test_sample_profile_min_and_max_are_written_like_every_other_number(value: object, text: str) -> None:
    assert contract._stat(value) == text
    if isinstance(value, (int, float)):
        assert float(text.replace(",", "")) == value


def test_sample_profile_of_a_proposal_shows_min_and_max_with_separators(outside_tmp: Path) -> None:
    root = outside_tmp / "m"
    sample = outside_tmp / "s.csv"
    with open(sample, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows([["id", "amount"], ["a", "10750"], ["b", "897700"],
                                                      ["c", "1234567.5"]])
    r = run_cli("contract", "propose", str(root), "--table", "amounts", "--csv", str(sample), "--unit", "amount=KRW")
    assert r.returncode == 0, r.stdout + r.stderr
    data = common.read_json(modelfolder.contract_path(root, "amounts"))
    obs = next(c for c in data["columns"] if c["name"] == "amount")["observed"]
    md = _read(contract.md_path(root, "amounts"))
    row = next(ln for ln in md.splitlines() if ln.startswith("| amount | 3 |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert (cells[4], cells[5], cells[6]) == ("10,750", "1,234,567.5", "2,143,017.5"), row
    assert [float(cells[i].replace(",", "")) for i in (4, 5, 6)] == [obs["min"], obs["max"], obs["sum"]], row


def _profile_rows(md: str) -> dict[str, list[str]]:
    lines = md.splitlines()
    if "| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |" not in lines:
        return {}
    start = lines.index("| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |") + 2
    rows = {}
    for ln in lines[start:]:
        if not ln.startswith("|"):
            break
        cells = [c.strip() for c in ln.strip("|").split("|")]
        rows[cells[0]] = cells
    return rows


@pytest.mark.parametrize("folder", _shipped_folders(), ids=lambda p: p.name)
def test_shipped_contract_md_shows_min_and_max_as_stored_with_separators(folder: Path) -> None:
    """After `contract show` (v7 integrator): each numeric minimum and maximum of a shipped .md reads back as the
    stored value, and one of 1,000 or more has its thousands separators."""
    checked = 0
    for cpath in sorted((folder / "contracts").glob("*.contract.json")):
        data = common.read_json(cpath)
        rows = _profile_rows(_read(cpath.with_name(cpath.name.replace(".contract.json", ".contract.md"))))
        for col in data["columns"]:
            obs = col.get("observed")
            if not isinstance(obs, dict) or col["name"] not in rows:
                continue
            for i, key in ((4, "min"), (5, "max")):
                value = obs.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                text = rows[col["name"]][i]
                assert float(text.replace(",", "")) == value, (cpath.name, col["name"], key, text)
                if abs(value) >= 1000:
                    assert "," in text, (cpath.name, col["name"], key, text)
                checked += 1
    assert checked > 0, folder


# ----------------------------------------------------------------------------- requests v7_P8 R2 and R3 ([C-sol-8])

def test_google_sheets_step_3_and_the_land_docstring_name_both_header_lines() -> None:
    gs = _flat(_read(REFS / "google-sheets.md"))
    assert "(column names and order," not in gs
    assert ("checks the CSV against the contract (the header line: the contract's names in the contract's order, or "
            "the sample headers the contract recorded, [column-contract.md](column-contract.md#sample-headers-and-"
            "the-header-line-land-accepts); types, nulls, allowed values, key, row count and control totals)") in gs
    doc = _flat(land.__doc__ or "")
    assert "(column names and order," not in doc
    assert ("the header line: the contract's names in the contract's order, or the sample headers the contract "
            "recorded") in doc


# ----------------------------------------------------------------------------- request v7_P8 R1 ([A-aera-3, C-sol-2])

GUARD = ("The raw-sheet guard refuses a shell command that copies onto an existing workbook, so the user copies the "
         "two files back by hand.")


@pytest.mark.parametrize("path", [REFS / "excel-power-query.md", REFS / "model-spec-format.md", SKILL / "SKILL.md",
                                  PLUGIN / "skills" / "build" / "SKILL.md"], ids=lambda p: p.parent.name + "/" + p.name)
def test_documents_of_the_kept_copies_say_who_copies_them_back(path: Path) -> None:
    if not path.is_file():
        pytest.skip(f"{path.name} is absent (the skill is used without its plugin)")
    assert GUARD in _flat(_read(path)), path


# ----------------------------------------------------------------------------- checks.md: the per-cell example

def test_checks_md_per_cell_example_is_the_worked_examples_block_and_check() -> None:
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", _read(REFS / "checks.md"), flags=re.S)]
    example = [b for b in blocks if any(x.get("id") == "pct_cell_diff" for x in b.get("blocks", []))]
    assert len(example) == 1, len(example)
    spec = common.read_json(SKILL / "examples" / "sales_vs_target" / "model.spec.json")
    shipped_blocks = {b["id"]: b for s in spec["sheets"] for b in s.get("blocks") or []}
    shipped_checks = {c["id"]: c for s in spec["sheets"] for c in s.get("checks") or []}
    assert [b["id"] for b in example[0]["blocks"]] == ["pct_cell_diff"]
    assert [c["id"] for c in example[0]["checks"]] == ["pct_cell_ties"]
    assert example[0]["blocks"][0] == shipped_blocks["pct_cell_diff"]
    assert example[0]["checks"][0] == shipped_checks["pct_cell_ties"]


# ----------------------------------------------------------------------------- request v7_P2 R1: the counts

def _error_checks(folder: Path) -> int:
    data = manifest.load_manifest(folder / "model.xlsx")
    return len([c for c in data["checks"] if c.get("severity", "error") == "error"])


def _summary_line(folder: Path) -> str:
    report = common.read_json(folder / "reports" / "check.json")
    return next(n for n in report.get("notes") or [] if n.startswith("checks: "))


@needs_plugin
def test_template_library_counts_equal_the_checks_of_each_folder() -> None:
    """Each count of templates/README.md equals the error checks of the folder's built manifest and the summary
    line of its reports/check.json (written by check --strict with Excel at the regeneration)."""
    lib = _read(PLUGIN / "templates" / "README.md")
    found = dict(re.findall(r"^\| `([a-z_]+)` \|.* \| (\d+) of \d+ hold \|$", lib, flags=re.M))
    assert set(found) == {"budget_vs_actual", "rolling_forecast", "price_volume_mix", "ar_aging"}, found
    for name, count in found.items():
        folder = PLUGIN / "templates" / name
        n = _error_checks(folder)
        assert f"| {n} of {n} hold |" in lib and int(count) == n, (name, count, n)
        assert _summary_line(folder) == f"checks: {n} of {n} hold", name
    flat = _flat(lib)
    example = SKILL / "examples" / "sales_vs_target"
    n = _error_checks(example)
    assert (f"skills/rule-of-thumb-model/examples/sales_vs_target ({n} of {n} hold; alerts: 0 of 1 show a value)"
            in flat), n
    assert _summary_line(example) == f"checks: {n} of {n} hold; alerts: 0 of 1 show a value"
    starter = SKILL / "templates" / "starter"
    n = _error_checks(starter)
    assert f"its checks; {n} of {n} hold)" in flat, n
    assert _summary_line(starter) == f"checks: {n} of {n} hold"

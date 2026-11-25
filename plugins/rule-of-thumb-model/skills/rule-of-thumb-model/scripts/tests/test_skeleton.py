"""Skeleton tests: every module imports, the CLI registry is complete, the stdlib-only modules stay stdlib-only,
the dictionary has a source for every value, the plugin and skill files are well formed (design sections 1 to 3, 10, 20).

Owner: INTEGRATOR (first version by the architect). These tests stay valid after the builders' work.
"""
from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
sys.path.insert(0, str(SCRIPTS))

import rotm  # noqa: E402

LIB_MODULES = ("common", "modelfolder", "schemas", "spec", "dictionary", "workbook", "manifest", "formula",
               "functions", "excel", "styles", "classify", "theme", "build", "land", "audit", "contract", "check",
               "compat", "trace", "split", "lift", "diff", "document", "selftest")
STDLIB_ONLY = ("common", "modelfolder", "schemas")
COMMAND_SKILLS = ("contract", "build", "theme", "audit", "check", "compat", "trace", "split", "lift", "diff", "document")
F12_TYPES = ("input", "calc", "link", "import", "check", "note", "col_header", "row_header", "raw", "title", "total",
             "output", "label", "unit")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


@pytest.mark.parametrize("name", LIB_MODULES)
def test_every_lib_module_imports(name: str) -> None:
    importlib.import_module(f"lib.{name}")


@pytest.mark.parametrize("name", STDLIB_ONLY)
def test_stdlib_only_modules_import_under_isolated_mode(name: str) -> None:
    code = (f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import lib.{name}; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('openpyxl', 'pandas')]; "
            "sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("command", sorted(rotm.COMMANDS))
def test_registry_entry_has_its_functions(command: str) -> None:
    module_name, prefix, _help, _owner = rotm.COMMANDS[command]
    module = importlib.import_module(f"lib.{module_name}")
    assert callable(getattr(module, f"add_{prefix}_arguments"))
    assert callable(getattr(module, f"run_{prefix}"))


@pytest.mark.parametrize("command", sorted(rotm.COMMANDS))
def test_every_subcommand_help_exits_0(command: str) -> None:
    proc = run_cli(command, "-h")
    assert proc.returncode == 0, proc.stderr
    assert f"rotm.py {command}" in proc.stdout


def test_usage_lists_every_subcommand() -> None:
    proc = run_cli()
    assert proc.returncode == 0
    for command in rotm.COMMANDS:
        assert re.search(rf"^\s+{command}\s", proc.stdout, re.M), command


def test_version_and_unknown_subcommand() -> None:
    assert run_cli("--version").stdout.strip() == "rule-of-thumb-model 0.1.0"
    proc = run_cli("buidl")
    assert proc.returncode == 2
    assert "did you mean 'build'" in proc.stderr


def test_formula_audit_entry_help() -> None:
    proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "formula_audit.py"), "-h"],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "--strict" in proc.stdout


def _leaves(node: object, path: str = ""):
    if isinstance(node, dict):
        if set(node) >= {"value", "source"}:
            yield path, node
            return
        for key, child in node.items():
            yield from _leaves(child, f"{path}.{key}" if path else key)


def test_dictionary_every_value_has_a_source() -> None:
    data = json.loads((SKILL / "references" / "style_dictionary.json").read_text(encoding="utf-8"))
    assert data["rotm_dictionary"] == 1
    for section in ("font", "sizes", "sheet_types", "colors", "number_formats", "cell_types", "rules"):
        leaves = list(_leaves(data[section], section))
        assert leaves, section
        for path, leaf in leaves:
            assert isinstance(leaf["source"], str) and leaf["source"].strip(), path
    for name in F12_TYPES:
        assert name in data["cell_types"], name


def test_dictionary_fixed_points_of_max() -> None:
    data = json.loads((SKILL / "references" / "style_dictionary.json").read_text(encoding="utf-8"))
    assert data["font"]["value"] == "Arial"
    assert data["sizes"]["dense"]["value"] == 8 and data["sizes"]["spaced"]["value"] == 9
    col = data["colors"][data["cell_types"]["col_header"]["fill"]["value"]]["value"]
    row = data["colors"][data["cell_types"]["row_header"]["fill"]["value"]]["value"]
    luminance = lambda hexv: sum(int(hexv[i:i + 2], 16) for i in (0, 2, 4))  # noqa: E731
    assert col[0:2] == col[2:4] == col[4:6], "column header fill is a grey"
    assert row[0:2] == row[2:4] == row[4:6], "row header fill is a grey"
    assert luminance(row) > luminance(col), "row headers are a lighter grey than column headers"


def test_depth_limit_is_one_setting() -> None:
    from lib import formula
    data = json.loads((SKILL / "references" / "style_dictionary.json").read_text(encoding="utf-8"))
    assert data["rules"]["depth_limit"]["value"] == formula.DEPTH_LIMIT_DEFAULT == 2
    assert data["rules"]["length_limit"]["value"] == formula.LENGTH_LIMIT_DEFAULT


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    block = text.split("---\n", 2)[1]
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip('"')
    return out


def test_main_skill_frontmatter() -> None:
    fm = _frontmatter(SKILL / "SKILL.md")
    assert fm["name"] == "rule-of-thumb-model"
    assert fm.get("disable-model-invocation", "false") != "true"
    assert len(fm["description"]) <= 1024


@pytest.mark.skipif(not (PLUGIN / ".claude-plugin" / "plugin.json").is_file(), reason="skill copied without its plugin")
@pytest.mark.parametrize("name", COMMAND_SKILLS)
def test_command_skills_are_user_only(name: str) -> None:
    fm = _frontmatter(PLUGIN / "skills" / name / "SKILL.md")
    assert fm["name"] == name
    assert fm["disable-model-invocation"] == "true"


@pytest.mark.skipif(not (PLUGIN / ".claude-plugin" / "plugin.json").is_file(), reason="skill copied without its plugin")
def test_plugin_manifest() -> None:
    data = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "rule-of-thumb-model"
    assert data["version"] == "0.1.0"
    assert data["description"]


# ----------------------------------------------------------------------------- SKILL.md, command skills, README

def _skill_texts() -> dict[str, str]:
    out = {"SKILL.md": (SKILL / "SKILL.md").read_text(encoding="utf-8")}
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        for name in COMMAND_SKILLS:
            out[f"skills/{name}/SKILL.md"] = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        out["README.md"] = (PLUGIN / "README.md").read_text(encoding="utf-8")
    return out


def test_main_skill_has_the_quality_bar_sections() -> None:
    """drawio-drill-updown's level (spec 2.6; plan F26): overview, inputs, a numbered procedure covering P1 to P7
    of spec 2.2 by name, commands, quick reference, common mistakes, what is and is not verified."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for heading in ("## Overview", "## Inputs", "## Procedure", "## Commands", "## Quick reference",
                    "## Common mistakes", "## What is and is not verified", "## File map"):
        assert heading in text, heading
    for step in ("P1 Agree the column contract", "P2 Land raw data", "P3 Model with formulas only",
                 "P4 Keep every formula short", "P5 Check", "P6 Style from the dictionary",
                 "P7 Audit, recalculate, look"):
        assert step in text, step
    assert "skeleton" not in text.lower() and "stub" not in text.lower()


def test_every_subcommand_named_in_the_skills_exists() -> None:
    bad = []
    for name, text in _skill_texts().items():
        for m in re.finditer(r"\b(?:ROTM|rotm\.py)\s+([a-z][a-z-]*)", text):
            if m.group(1) not in rotm.COMMANDS and m.group(1) not in ("subcommand",):
                bad.append(f"{name}: {m.group(1)}")
    assert not bad, f"unknown subcommands: {bad}"


def test_every_reference_file_named_in_the_skills_exists() -> None:
    missing = []
    for name, text in _skill_texts().items():
        for ref in set(re.findall(r"references/([\w.-]+\.(?:md|json))", text)):
            if not (SKILL / "references" / ref).is_file():
                missing.append(f"{name}: references/{ref}")
    assert not missing, missing


def test_every_flag_named_in_a_command_skill_exists() -> None:
    """A `--flag` written next to a subcommand in a command skill must be an option of that subcommand."""
    if not (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        pytest.skip("skill copied without its plugin")
    helps: dict[str, str] = {}
    bad = []
    for name in COMMAND_SKILLS:
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        for m in re.finditer(r"`ROTM ([a-z]+)(?: (propose|agree|show|status|remove))?([^`]*)`", text):
            sub, action, rest = m.group(1), m.group(2), m.group(3)
            key = f"{sub} {action}" if action else sub
            if key not in helps:
                args = [sub, action, "-h"] if action else [sub, "-h"]
                helps[key] = run_cli(*args).stdout
            for flag in re.findall(r"(--[a-z][a-z-]*)", rest):
                if flag not in helps[key]:
                    bad.append(f"skills/{name}: rotm.py {key} {flag}")
    assert not bad, bad


@pytest.mark.skipif(not (PLUGIN / ".claude-plugin" / "plugin.json").is_file(), reason="skill copied without its plugin")
def test_readme_names_every_command_and_hook_and_both_install_ways() -> None:
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    for name in COMMAND_SKILLS:
        assert f"/rule-of-thumb-model:{name}" in text, name
    for hook in ("contract gate", "raw-sheet guard", "audit on write", "finish check"):
        assert hook in text, hook
    assert "--plugin-dir" in text and "/plugin install" in text and "marketplace" in text
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for script in ("contract_gate.py", "raw_guard.py", "audit_on_write.py", "finish_check.py"):
        assert script in json.dumps(hooks), script


@pytest.mark.skipif(not (PLUGIN / ".claude-plugin" / "plugin.json").is_file(), reason="skill copied without its plugin")
@pytest.mark.parametrize("name", COMMAND_SKILLS)
def test_command_skill_sections(name: str) -> None:
    text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    fm = _frontmatter(PLUGIN / "skills" / name / "SKILL.md")
    assert fm["description"].startswith(f"Invoked by the user only as /rule-of-thumb-model:{name}")
    assert fm.get("argument-hint")
    for heading in ("## Names", "## Read first", "## Steps", "## Exit codes", "## What to tell the user"):
        assert heading in text, heading
    assert "skeleton" not in text.lower() and "stub" not in text.lower()

"""Tests of the v2 documents and of what they state (package P8 of the v2 round, 2026-11-25). Owner: P8.

- J-2 part 2: the checks of test_references.py that need the project's design file or research folder run only when
  ROTM_SOURCE_TREE names the source tree; a sibling folder does not switch them on.
- J-3: on a land refusal the documents say: report the rows, stop, agree a changed contract again; a known
  exception keeps the workbook's key check and null check, which still count the rows (Excel).
- C-sol-15: SKILL.md states the render limits that lib/excel.py uses, and what to do beyond them.
- O-1 and O-3: README uses placeholders for every install path; no SKILL.md, hooks.json, hook script or library
  script holds an absolute path that the plugin uses at run time.
- J-5 (v3, J-7; v4, J-3): SKILL.md and README list the same runs made on the final v4 files.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v2_docs.py -q -p no:cacheprovider
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
DATA = TESTS / "data" / "build"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import build, check, land, manifest  # noqa: E402
from test_build import base_contract, base_spec, make_model  # noqa: E402

PY = [sys.executable, "-P", "-X", "utf8", "-B"]


def _skill_md() -> str:
    return (SKILL / "SKILL.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text from `heading` to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading)
    rest = text[start + len(heading):]
    m = re.search(r"^#{1,%d} " % level, rest, flags=re.M)
    return heading + (rest[:m.start()] if m else rest)


def _procedure_step(text: str, number: int) -> str:
    """Step `number` of SKILL.md's numbered procedure (from '<n>. **' to the next step or heading)."""
    proc = _section(text, "## Procedure")
    m = re.search(rf"^{number}\. \*\*", proc, flags=re.M)
    assert m, f"SKILL.md: no procedure step {number}"
    rest = proc[m.start():]
    n = re.search(rf"^{number + 1}\. \*\*", rest, flags=re.M)
    return rest[:n.start()] if n else rest


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# ----------------------------------------------------------------------------- J-2 part 2: ROTM_SOURCE_TREE

SOURCE_TESTS = ("test_confirmed_labels_match_the_claim_checks", "test_surveyed_workbook_names_are_in_the_research_catalogue")


def _run_reference_tests(tmp_path: Path, env_value: str | None) -> dict[str, str]:
    """Run the source-tree tests of test_references.py in a subprocess; return {test name: passed|skipped|failed}."""
    env = {k: v for k, v in os.environ.items() if k != "ROTM_SOURCE_TREE"}
    if env_value is not None:
        env["ROTM_SOURCE_TREE"] = env_value
    xml = tmp_path / "refs.xml"
    proc = subprocess.run(PY + ["-m", "pytest", str(TESTS / "test_references.py"), "-q", "-p", "no:cacheprovider",
                                "-k", " or ".join(SOURCE_TESTS), f"--junitxml={xml}"],
                          cwd=SKILL, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert xml.is_file(), proc.stdout[-2000:] + proc.stderr[-2000:]
    out: dict[str, str] = {}
    for case in ET.parse(xml).getroot().iter("testcase"):
        name = case.get("name", "").split("[")[0]
        state = "passed"
        if case.find("skipped") is not None:
            state = "skipped"
        elif case.find("failure") is not None or case.find("error") is not None:
            state = "failed"
        prev = out.get(name)
        out[name] = state if prev in (None, state) else ("failed" if "failed" in (prev, state) else "mixed")
    return out


def test_source_tree_checks_skip_without_ROTM_SOURCE_TREE(tmp_path: Path) -> None:
    """Without the variable the checks skip, even where the project's research folder lies next to the plugin."""
    states = _run_reference_tests(tmp_path, None)
    assert set(SOURCE_TESTS) <= set(states), states
    assert all(states[n] == "skipped" for n in SOURCE_TESTS), states


def test_source_tree_checks_run_when_ROTM_SOURCE_TREE_is_set(tmp_path: Path) -> None:
    """A variable that names a folder without the research files makes the checks fail, not skip: the variable,
    not a sibling folder, switches them on."""
    empty = tmp_path / "not_a_source_tree"
    empty.mkdir()
    states = _run_reference_tests(tmp_path, str(empty))
    assert all(states.get(n) == "failed" for n in SOURCE_TESTS), states


# ----------------------------------------------------------------------------- J-3: documents

def test_skill_md_says_what_to_do_when_land_refuses() -> None:
    text = _skill_md()
    land_step = _flat(_procedure_step(text, 2))
    for needed in ("refuses", "report", "rows", "stop", "contract change", "agreed again", "new words",
                   "--key-repeats", "--known-nulls", "L-KNOWN"):
        assert needed in land_step, f"SKILL.md P2: missing '{needed}'"
    for change in ("key", "null rule", "row count", "control total"):
        assert change in land_step, f"SKILL.md P2: a change of {change} must be named as a contract change"
    mistakes = _flat(_section(text, "## Common mistakes"))
    assert "refus" in mistakes and "made-up key" in mistakes and "agreed again" in mistakes


def test_column_contract_md_says_what_to_do_when_land_refuses() -> None:
    text = (SKILL / "references" / "column-contract.md").read_text(encoding="utf-8")
    section = _flat(_section(text, "## When land refuses an extract"))
    for needed in ("report", "rows", "stop", "made-up key", "null rule", "row count", "control total",
                   "contract change", "agreed again", "known exception", "<t>_keys", "<t>_nulls_<col>", "L-KNOWN"):
        assert needed in section, f"column-contract.md 'When land refuses an extract': missing '{needed}'"


# ----------------------------------------------------------------------------- J-3: the checks still count the rows

def _write_csv(path: Path, header: list[str], body: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(body)
    return path


def _defective_extract(tmp_path: Path) -> Path:
    """The fixture's 72 lines with line 22 delivered twice (the copy is line 74) and the amount of line 12 empty."""
    with open(DATA / "sales.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], [list(r) for r in rows[1:]]
    body[10][5] = ""                       # CSV line 12: amount empty
    body.append(list(body[20]))            # CSV line 22 delivered again as line 74
    return _write_csv(tmp_path / "defective.csv", header, body)


def _contract_with_known_exceptions() -> dict:
    c = base_contract()
    c.pop("agreement", None)
    c["expect"] = {"rows": {"min": 1, "max": 1000},
                   "key_repeats": {"max": 1, "note": "line delivered twice by the source; agreed by the user"}}
    for col in c["columns"]:
        if col["name"] == "amount":
            col["known_nulls"] = {"max": 1, "note": "one line without an amount; agreed by the user"}
    return c


@pytest.fixture(scope="module")
def known_exception_model(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, object]:
    tmp = tmp_path_factory.mktemp("j3")
    root = make_model(tmp / "m", contract=_contract_with_known_exceptions(), spec=base_spec(), land_it=False)
    rep = land.land_csv(root, "sales", _defective_extract(tmp))
    return root, rep


def test_land_names_the_lines_of_each_known_exception(known_exception_model) -> None:
    _root, rep = known_exception_model
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    known = [f for f in rep.findings if f.rule == "L-KNOWN"]
    assert len(known) == 2, [f.line() for f in rep.findings]
    key = next(f for f in known if "repeat" in f.message)
    null = next(f for f in known if "empty" in f.message)
    assert re.search(r"\b22\b", key.message) and re.search(r"\b74\b", key.message), key.message
    assert re.search(r"\b12\b", null.message), null.message


@pytest.mark.excel
def test_known_exceptions_keep_the_key_and_null_checks_counting(known_exception_model) -> None:
    """J-3: the rows the user accepted are landed, and after recalculation in Excel the workbook's own checks
    `sales_keys` and `sales_nulls_amount` show actual 1 each (at most 1 agreed), so the defects stay visible."""
    root, _rep = known_exception_model
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    man = manifest.load_manifest(root / "model.xlsx")
    kinds = {c["id"]: c.get("kind") for c in man["checks"]}
    assert kinds["sales_keys"] == "at_most" and kinds["sales_nulls_amount"] == "at_most", kinds
    res = check.check_model(root, strict=True)
    assert res.data.get("recalculated") is True, [f.line() for f in res.findings]
    by_id = {c["id"]: c for c in res.data["checks"]}
    for cid in ("sales_keys", "sales_nulls_amount"):
        entry = by_id[cid]
        assert entry["actual"] == 1, entry
        assert entry["state"] == "pass", entry
    assert res.exit_code == 0, [f.line() for f in res.findings]


# ----------------------------------------------------------------------------- C-sol-15: render limits

def test_skill_md_states_the_render_limits_lib_excel_uses() -> None:
    import inspect
    from lib import excel
    sig = inspect.signature(excel.render_sheets)
    rows, cols = sig.parameters["max_rows"].default, sig.parameters["max_cols"].default
    last_col = ""
    n = cols
    while n:
        n, r = divmod(n - 1, 26)
        last_col = chr(65 + r) + last_col
    step = _flat(_procedure_step(_skill_md(), 7))
    assert f"rows 1 to {rows}" in step and f"columns A to {last_col}" in step, (rows, last_col)
    for needed in ("--max-rows", "--max-cols", "truncated", "not looked at", "raw and helper sheets",
                   f"first {rows} rows"):
        assert needed in step.lower(), f"SKILL.md P7: missing '{needed}'"


# ----------------------------------------------------------------------------- O-1, O-3: install paths

# A drive-letter path, a network path (\\server\share) or a POSIX home or temporary folder. Not matched: a path that
# starts with a placeholder (<model folder>\raw), a variable (${CLAUDE_PLUGIN_ROOT}/hooks, %LOCALAPPDATA%\...).
ABSOLUTE = re.compile(r"(?<![\w$<{>%\\])(?:[A-Za-z]:[\\/]|\\\\[A-Za-z][\w.-]*\\[\w$.-]+|/(?:Users|home|mnt|tmp)/)")


@pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")
def test_readme_install_uses_placeholders() -> None:
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    install = _section(text, "## Install")
    for needed in ("<plugin folder>", "<marketplace folder>", "<owner>/<repo>", "--plugin-dir",
                   "/plugin marketplace add", "/plugin install", "${CLAUDE_PLUGIN_ROOT}"):
        assert needed in install, f"README Install: missing '{needed}'"
    hits = [m.group(0) for m in ABSOLUTE.finditer(text)]
    assert not hits, f"README holds absolute paths: {hits}"


def _runtime_files() -> list[Path]:
    files = [SKILL / "SKILL.md"]
    files += sorted((SCRIPTS / "lib").glob("*.py")) + sorted((SCRIPTS / "lib").glob("*.ps1"))
    files += [SCRIPTS / "rotm.py", SCRIPTS / "formula_audit.py"]
    if HAS_PLUGIN:
        files += sorted((PLUGIN / "skills").glob("*/SKILL.md"))
        files += sorted((PLUGIN / "hooks").glob("*.py")) + sorted((PLUGIN / "hooks").glob("*.sh"))
        files += [PLUGIN / "hooks" / "hooks.json"]
    return sorted(set(files))


# Absolute paths that occur in these files as examples in a docstring or a help text, or as Windows' own system
# folder when the SystemRoot variable is missing. None of them is a path the plugin reads or writes.
EXAMPLE_PATHS = {
    ("common.py", "C:/"): "docstring: how a path is written in both slash styles",
    ("formula.py", "C:/"): "docstring: an external workbook reference",
    ("excel.py", "C:\\"): "fallback for %SystemRoot% (Windows' own folder, the same on every Windows PC)",
    # v5 (P8 for P1's A-aera-8, C-sol-8): a comment that names the forms of a path x_open_finding shortens
    ("excel.py", "\\\\server\\share"): "comment: the form of a network share path in an Excel error text",
}


def test_no_absolute_path_in_skills_hooks_and_scripts() -> None:
    """O-3: every path the skills, hooks and scripts use is relative to the plugin or skill folder, comes from
    ${CLAUDE_PLUGIN_ROOT} or from an environment variable."""
    bad = []
    for path in _runtime_files():
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in ABSOLUTE.finditer(line):
                if (path.name, m.group(0)) in EXAMPLE_PATHS:
                    continue
                bad.append(f"{path.relative_to(PLUGIN if HAS_PLUGIN else SKILL).as_posix()}:{no}: {line.strip()[:120]}")
    assert not bad, bad


@pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")
def test_every_hook_command_starts_from_the_plugin_root() -> None:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = [h["command"] for event in hooks["hooks"].values() for group in event for h in group["hooks"]]
    assert commands
    for cmd in commands:
        scripts = re.findall(r'"([^"]+\.(?:py|sh))"', cmd)
        assert scripts, cmd
        assert all(s.startswith("${CLAUDE_PLUGIN_ROOT}/hooks/") for s in scripts), cmd


# ----------------------------------------------------------------------------- J-5, v3 J-7, v4 to v6 J-3: one statement of the runs

RUNS_HEADING = "### Runs on the final v6 files"


def _run_bullets(text: str) -> list[str]:
    """The bullets of the subsection 'Runs on the final v6 files' of 'What is and is not verified', each as one
    line (continuation lines joined)."""
    section = _section(text, "## What is and is not verified")
    assert RUNS_HEADING in section, f"'What is and is not verified' has no subsection '{RUNS_HEADING}'"
    runs = _section(section, RUNS_HEADING)[len(RUNS_HEADING):]
    bullets: list[str] = []
    for line in runs.splitlines():
        if line.startswith("- "):
            bullets.append(line[2:].strip())
        elif line.startswith("  ") and line.strip() and bullets:
            bullets[-1] += " " + line.strip()
    return bullets


@pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")
def test_skill_md_and_readme_state_the_same_selftest_run() -> None:
    """v3, J-7; v4 to v6, J-3: SKILL.md and README.md list the same runs made on the final v6 files (equal lists
    of bullets)."""
    skill = _run_bullets(_skill_md())
    readme = _run_bullets((PLUGIN / "README.md").read_text(encoding="utf-8"))
    assert skill, "SKILL.md states no run made on the final v6 files"
    assert skill == readme

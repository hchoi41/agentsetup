"""Shared test setup for rule-of-thumb-model (design section 21). Owner: INTEGRATOR (first version by the architect).

- Puts SKILL/scripts on sys.path, so tests import `from lib import formula`.
- Skips tests marked `excel` when Excel is not available (or lib.excel is not written yet, or ROTM_NO_EXCEL=1).
- Gives every test its own ROTM_STATE_DIR (hook session records, last_error.txt) but keeps the Excel lock at
  the one machine-wide path, so parallel test runs by different builders still run one Excel job at a time.
- Fixtures: skill_dir, plugin_dir, foreign_dir, model_dir, outside_tmp (v5: a temporary folder outside the plugin,
  wherever --basetemp points).
Builders put their own fixtures in their own test files; a change here goes through BUILD/requests/.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True   # no __pycache__ folders inside the plugin (F02 copies the skill folder as it is)

import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
BUILD_FOREIGN = SKILL.parents[2] / "build" / "foreign"     # 20.PROCESS/build/foreign during the overnight build

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _default_lock() -> Path:
    base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home()
    name = "rule-of-thumb-model" if os.environ.get("LOCALAPPDATA") else ".rule-of-thumb-model"
    return base / name / "excel.lock"


def _excel_available() -> tuple[bool, str]:
    if os.environ.get("ROTM_NO_EXCEL") == "1":
        return False, "ROTM_NO_EXCEL=1"
    try:
        from lib import excel
        status = excel.excel_status()
    except NotImplementedError:
        return False, "lib.excel is not written yet"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return bool(status.available), status.reason or ""


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # get_closest_marker, not `"excel" in item.keywords`: keywords also hold parametrize ids such as [excel].
    marked = [item for item in items if item.get_closest_marker("excel") is not None]
    if not marked:
        return
    ok, why = _excel_available()
    if ok:
        return
    skip = pytest.mark.skip(reason=f"Excel not available: {why}")
    for item in marked:
        item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    if not os.environ.get("ROTM_EXCEL_LOCK"):
        monkeypatch.setenv("ROTM_EXCEL_LOCK", str(_default_lock()))
    state = tmp_path_factory.mktemp("rotm_state")
    monkeypatch.setenv("ROTM_STATE_DIR", str(state))
    return state


@pytest.fixture
def skill_dir() -> Path:
    return SKILL


@pytest.fixture
def plugin_dir() -> Path | None:
    return PLUGIN if (PLUGIN / ".claude-plugin" / "plugin.json").is_file() else None


@pytest.fixture
def foreign_dir() -> Path:
    """Copies of workbooks the plugin did not make (ROTM_FOREIGN_DIR, else BUILD/foreign); skip when none."""
    env = os.environ.get("ROTM_FOREIGN_DIR")
    folder = Path(env) if env else BUILD_FOREIGN
    if not folder.is_dir() or not any(folder.glob("*.xls*")):
        pytest.skip(f"no foreign workbooks in {folder}")
    return folder


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    """An empty model folder with its marker (design section 4.1)."""
    root = tmp_path / "model"
    from lib import modelfolder
    modelfolder.init_model(root, name="test_model")
    return root


def _inside_the_plugin(path: Path) -> bool:
    """True when `path` lies in the skill folder or in the plugin folder around it."""
    roots = [SKILL] + ([PLUGIN] if (PLUGIN / ".claude-plugin" / "plugin.json").is_file() else [])
    p = os.path.normcase(str(path.resolve()))
    for r in roots:
        base = os.path.normcase(str(r.resolve()))
        if p == base or p.startswith(base.rstrip("\\/") + os.sep):
            return True
    return False


@pytest.fixture
def outside_tmp(tmp_path: Path) -> Iterator[Path]:
    """A temporary folder outside the skill and plugin folders, for a test of a rule that holds only outside them
    (the 'example' agreement channel, the portable form of a folder elsewhere). v5 (r4 verdict [A-aera-8, C-sol-8]:
    the suite passes wherever pytest's temporary folder is): it is tmp_path when tmp_path lies outside them; with
    --basetemp inside the skill folder it is a new folder in the system's temporary folder, removed after the test."""
    if not _inside_the_plugin(tmp_path):
        yield tmp_path
        return
    folder = Path(tempfile.mkdtemp(prefix="rotm_outside_"))
    try:
        if _inside_the_plugin(folder):
            pytest.skip(f"no temporary folder outside the plugin folder: {folder} lies inside it")
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)

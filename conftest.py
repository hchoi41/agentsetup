"""Root conftest — adds scripts/ to sys.path so all test suites can import
career_db, memorylib, and other script packages directly."""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def db_path(tmp_path):
    # tmp_path is never under OneDrive/13.MEMORY_HDD, so guards pass.
    d = tmp_path / "memory_runtime" / "db"
    d.mkdir(parents=True)
    return str(d / "memory.db")

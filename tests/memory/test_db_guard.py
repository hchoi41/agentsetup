import pytest

from memorylib import db


def test_guard_allows_local_runtime_path(db_path):
    db.assert_safe_db_path(db_path)   # tmp memory_runtime path -> no raise


def test_guard_refuses_onedrive_path(monkeypatch, tmp_path):
    onedrive = tmp_path / "OneDrive"
    (onedrive / "db").mkdir(parents=True)
    monkeypatch.setenv("OneDrive", str(onedrive))
    with pytest.raises(db.UnsafeDbLocation):
        db.assert_safe_db_path(str(onedrive / "db" / "memory.db"))


def test_guard_refuses_append_only_hdd(tmp_path):
    bad = tmp_path / "13.MEMORY_HDD" / "db" / "memory.db"
    with pytest.raises(db.UnsafeDbLocation):
        db.assert_safe_db_path(str(bad))

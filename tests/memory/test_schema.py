from memorylib import db


def test_connect_sets_wal_and_fk(db_path):
    conn = db.connect(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_v1a_ddl_creates_all_tables_and_views(db_path):
    conn = db.connect(db_path)
    conn.executescript(db.V1A_DDL)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view','index')")}
    for t in ("sessions", "events", "facts", "artifacts", "ops_log", "memory_fts",
              "events_local", "facts_local", "idx_facts_valid"):
        assert t in names

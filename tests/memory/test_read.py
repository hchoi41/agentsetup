import pytest
from memorylib import db, store, read


def test_query_rejects_writes_and_pragma_and_attach(db_path):
    db.init(db_path); conn = db.connect(db_path); store.fact_add(conn, "x", "c")
    assert read.run_query(db_path, "SELECT count(*) FROM facts")[0][0] == 1
    for bad in ["INSERT INTO facts(statement,category) VALUES('y','c')",
                "UPDATE facts SET valid=0", "DROP TABLE facts",
                "PRAGMA journal_mode=DELETE", "ATTACH DATABASE 'x.db' AS x",
                "SELECT 1; DROP TABLE facts", "SELECT load_extension('x')"]:
        with pytest.raises(Exception):
            read.run_query(db_path, bad)


def test_brief_returns_current_facts(db_path):
    db.init(db_path); conn = db.connect(db_path)
    store.fact_add(conn, "current fact", "c")
    b = read.brief(conn, days=3650)
    assert any("current fact" in f["statement"] for f in b["facts"])


def test_recent_uses_local_day_window(db_path):
    db.init(db_path); conn = db.connect(db_path)
    e_old, _ = store.event_add(conn, "note", "stale")
    store.event_add(conn, "note", "fresh")
    conn.execute("UPDATE events SET ts=datetime('now','-10 days') WHERE id=?", (e_old,)); conn.commit()
    contents = [r["content"] for r in read.recent(conn, days=1)]
    assert "fresh" in contents and "stale" not in contents   # window filters; recent() exercised

from memorylib import db, store


def test_event_add_persists_indexes_and_logs(db_path):
    db.init(db_path); conn = db.connect(db_path)
    eid, hits = store.event_add(conn, "decision", "chose SQLite", project="hub")
    assert isinstance(eid, int) and hits == []
    assert conn.execute("SELECT content FROM events WHERE id=?", (eid,)).fetchone()[0] == "chose SQLite"
    assert conn.execute("SELECT count(*) FROM memory_fts WHERE src='event' AND ref_id=?", (eid,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM ops_log WHERE op='event_add'").fetchone()[0] == 1


def test_event_add_warns_on_secret(db_path):
    db.init(db_path); conn = db.connect(db_path)
    _, hits = store.event_add(conn, "note", "key " + "AKIA" + "ABCDEFGHIJKLMNOP")
    assert "aws_access_key_id" in hits   # warning surfaced, write still succeeds

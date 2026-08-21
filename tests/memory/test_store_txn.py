from memorylib import db, store


def test_event_add_after_dirty_dml_on_same_connection(db_path):
    db.init(db_path)
    conn = db.connect(db_path)
    conn.execute("INSERT INTO events(kind,content) VALUES('n','x')")

    store.event_add(conn, "note", "after dirty")

    contents = {r["content"] for r in conn.execute("SELECT content FROM events")}
    assert {"x", "after dirty"} <= contents
